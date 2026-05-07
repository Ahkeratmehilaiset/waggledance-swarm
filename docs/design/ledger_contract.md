# Dual-ledger contract — `phase_fix_ledger.json` vs `regression_ledger.json`

Phase 2B-Revision shipped two ledgers with overlapping conceptual
surface but different lifecycles, schemas, existence guarantees,
and reader/writer audiences. Phase 2B-R2 ARCH-006 is the
post-real-use observation that without an explicit contract,
contributors will reach for whichever ledger is closer to hand
and produce drift.

This file is the contract.

## TL;DR

| | `docs/design/phase_fix_ledger.json` | `state/regression_ledger.json` |
|---|---|---|
| **Purpose** | Committed historical record of every per-phase architectural finding + fix outcome. PR-visible. Read by future phases as primer. | Live runtime state machine of currently-tracked regressions in the local working tree. |
| **Lifecycle** | Append-only across phases. Rows transition status (`backlog` → `fixed` / `false_positive_due_to_truncation` / `not_reproducible` / `informational`). Never deleted. | Mutable. Entries created on FAILED iteration / critical or high finding; severity scoring + state transitions over the run. May be cleared between runs. |
| **Existence after `git clone`** | Always present (committed). | Absent until first runtime emit. Always recreated by runtime helpers (`Ensure-WaggleRegressionLedger`). |
| **Schema** | `schema_version: 1`. Rows: `tag` (e.g. `ARCH-001`, `REL-019`, `SEC-009`), `title`, `source`, `status`, `phase_introduced`, `phase_fixed_or_documented`, `canonical_source_anchors[]`, `tests[]`, `notes`. The unique key is `(phase_introduced, tag)`. | `schema_version: 1`. Entries: `entry_id`, `epoch_id`, `iteration_id`, `finding_id`, `severity`, `signature`, `score`, `state` (e.g. `seen` → `in_review` → `verified_fixed`), `created_at_utc`, `updated_at_utc`, `dedup_key`. |
| **Authoritative writer** | Human / Claude editing a PR. Validated by `Test-PhaseFixLedger.ps1`. | Runtime: `RegressionLedger.ps1` helpers, called from `Invoke-WaggleIteration` and `Invoke-WaggleReview` (Phase 2B-R2 P5c). Validated by `Test-RegressionLedger.ps1`. |
| **PR review surface** | YES. Diff is reviewable as part of any PR that adds/changes a row. | NO. File is gitignored via `.git/info/exclude` (`/state/`); never reaches the remote. |
| **Where to look first** | "Has this kind of finding ever appeared in any phase? What was the outcome?" | "Is this regression already known in the current run?" |

## Lifecycle in detail

### `phase_fix_ledger.json`

* **Stored at:** `docs/design/phase_fix_ledger.json` (committed).
* **Mirror file:** `docs/design/phase_fix_ledger.md` (rendered markdown table; produced/maintained alongside).
* **Cadence:** edited as part of a PR. Every architect / security / reliability internal review finding that the operator wants to durably track gets a row. Rows for accepted findings move from `backlog` (deferred) to `fixed` when a later phase implements the fix.
* **Tag namespacing:** `ARCH-NNN`, `REL-NNN`, `SEC-NNN` are NOT globally unique. The unique key is `(phase_introduced, tag)`. A finding tagged `ARCH-001` in Phase 2A-3 is a different row from `ARCH-001` in Phase 2B-R.
* **Anchors:** `canonical_source_anchors[]` use the `path :: stable_text` shape so they survive line-number drift. `Test-PhaseFixLedger.ps1` validates that the file at `path` actually contains `stable_text` for every `fixed` / `already_fixed` / `false_positive_due_to_truncation` row.
* **Backlog rows:** must include a future-phase target + an acceptance note in `notes` (validated by `Test-PhaseFixLedger.ps1`).
* **Validator:** `Test-PhaseFixLedger.ps1`, in the hardening gate suite.

### `state/regression_ledger.json`

* **Stored at:** `state/regression_ledger.json` (gitignored). The repo's `.gitignore` re-includes `orchestrator/lib/`; the `state/` exclusion lives in the local-only `.git/info/exclude` (per the trailing comment in the repo `.gitignore`).
* **Lifecycle entry-points:**
  * `Add-WaggleRegressionFromIterationFailure` — append/upsert an entry when an iteration ends in `FAILED` / `TIMEOUT` / similar non-success terminal state.
  * `Add-WaggleRegressionFromReviewFinding` — append/upsert an entry when an internal review surfaces a `critical` or `high` security/reliability finding.
  * Both helpers go through `Add-WaggleRegressionEntry` (the de-duped writer).
* **Dedup contract:** entries are de-duped by signature `(iteration_id + ":" + finding_id)` for review-driven entries and by signature `(iteration_id + ":" + execution_failure_kind)` for iteration-driven entries. Re-firing the same hook with the same signature does NOT double-add; instead the existing entry's `updated_at_utc` is bumped and `seen_count` is incremented.
* **State machine:** `seen` (newly observed) → `in_review` (active triage) → `verified_fixed` (later iteration verified the regression no longer reproduces) → `archived` (closed-out for the epoch). State transitions are gated through `Set-WaggleRegressionState`.
* **Severity scoring:** 0–100, derived from finding severity band (`info` < `low` < `medium` < `high` < `critical`) plus a small bonus for resurrection-after-fix.
* **Validator:** `Test-RegressionLedger.ps1`, in the hardening gate suite.

## How `Build-WaggleProposalMatrix.ps1` cross-links both

`Build-WaggleProposalMatrix.ps1` produces `proposal_matrix.json` /
`proposal_matrix.md`, which is the synthesizer's primary decision
surface. Each matrix row carries two cross-link fields:

| Field | Source | Resolution |
|-------|--------|------------|
| `linked_ledger_tags[]` | `docs/design/phase_fix_ledger.json` | Text-match: scans the proposal's `title` + `rationale` against committed ledger row titles + tags. Always available because `phase_fix_ledger.json` is committed. |
| `linked_regressions[]` | `state/regression_ledger.json` (if present) | Finding-id match: maps each proposal back to the runtime regression entries that share its source finding id. **May be empty** if the regression ledger does not exist (clean clone) or has been cleared. |

The matrix builder MUST tolerate the absence of
`state/regression_ledger.json` (treat `linked_regressions` as `[]`).
The matrix MUST always populate `linked_ledger_tags` from the
committed ledger, because that file always exists.

## What survives a clean clone

| File | Survives `git clone`? | Notes |
|------|----------------------|-------|
| `docs/design/phase_fix_ledger.json` | YES | Committed. |
| `docs/design/phase_fix_ledger.md` | YES | Committed. |
| `docs/design/ledger_contract.md` | YES (this file). | Committed. |
| `schemas/regression_ledger.schema.json` | YES | Committed. |
| `state/regression_ledger.json` | NO | Runtime-only. Recreated by `Ensure-WaggleRegressionLedger`. |
| `iterations/<id>/...` | NO | Runtime-only. |
| `transcripts/<id>/...` | NO | Runtime-only. |
| `orchestrator.config.json` | NO | Live config. The committed example is `orchestrator.config.example.json`. |

## When to write where

```
                         ┌────────────────────────────────────────────┐
                         │   You're touching the orchestrator surface │
                         └─────────────┬──────────────────────────────┘
                                       │
                                       ▼
       ┌──────────────────────────────────────────────────────────┐
       │ Is this a finding that should outlive this run / phase? │
       └─────┬──────────────────────────────────────┬─────────────┘
             │ YES                                  │ NO (just this run)
             ▼                                      ▼
     phase_fix_ledger.json                  regression_ledger.json
     (committed; PR-visible)                (state/; gitignored;
                                             written by hooks)
             │                                      │
             ▼                                      ▼
     Tag = ARCH/REL/SEC + phase            Signature = iteration_id +
     Anchors point at source.              finding_id (or failure kind).
     Validated by Test-PhaseFixLedger      Validated by Test-RegressionLedger
```

If you find yourself writing the same fact to both ledgers, pick
`phase_fix_ledger.json`. The runtime ledger is the right place
for *current-run state*, not for *durable phase outcomes*.

## See also

* `orchestrator/lib/RegressionLedger.ps1` — runtime ledger helpers.
* `orchestrator/Test-RegressionLedger.ps1` — validator + dedup proofs.
* `orchestrator/Test-PhaseFixLedger.ps1` — committed-ledger validator (anchors, backlog notes, MD/JSON parity).
* `orchestrator/Build-WaggleProposalMatrix.ps1` — the cross-link consumer.
* `docs/design/phase_fix_ledger.json` — source of truth for committed rows.
* `docs/design/phase_fix_ledger.md` — markdown rendering for PR review.
