# Phase 2A-4 backlog

Findings deliberately NOT fixed in Phase 2A-4. Each item below has a
documented rationale and a minimal acceptance criterion for the
phase that should pick it up. Phase 2A-4 chose surgical fixes per
master prompt rule 2 ("Do not broadly refactor stable Phase 2A-2/2A-3
core unless required to fix a confirmed high-risk bug").

## ARCH-002 -- subprocess runner duplication

**Risk:** medium-low. Two near-identical subprocess implementations
exist:

- `orchestrator/lib/ClaudeRunner.ps1::Invoke-ClaudeCodePrint`
  (Phase 1.6 / 2A-1, used by smoke flow)
- `orchestrator/Invoke-WaggleReview.ps1::Invoke-WaggleReviewSubprocess`
  (Phase 2A-2 special-case workaround for PS 5.1
  `Register-ObjectEvent` async-event stdout loss with fast-exit
  children)

**Why not in 2A-4:** master prompt rule 3 explicitly says do not
generalize `Invoke-WaggleReviewSubprocess` into a shared subprocess
library unless REL-005 cannot be fixed safely in place. REL-005
(timeout enforcement) was fixed in place, so consolidation is now
purely an architecture-cleanup task.

**Suggested phase:** Phase 2A-5 or later. A unified `Spawn-ClaudeCodeSubprocess`
helper that supports both the async-event mode (real Claude, slow,
many output events) and the synchronous mode (fast-exit fakes,
hangs need bounded-wait drains) with one set of timeout / lock
contracts.

**Minimal acceptance criteria:**

- One subprocess helper used by both entry points.
- Test-Phase2A4 + Test-ReviewSubprocessTimeout still green.
- Real Claude smoke + 3 reviews on the merged commit pass.
- No regression in Test-ClaudeRunner.

## ARCH-003 -- entry-point dot-source order

**Risk:** low. Both `Invoke-WaggleIteration.ps1` and
`Invoke-WaggleReview.ps1` dot-source ~10 lib files in a hardcoded
order. Reordering is fragile and not currently tested.

**Why not in 2A-4:** purely organisational. No correctness bug. PS
5.1 dot-source is order-sensitive only because some libs reference
helpers from other libs at script-load time; that ordering is
already correct and stable.

**Suggested phase:** Phase 2A-5. Either (a) introduce a small
`Import-WaggleOrchestratorLibs` helper that owns the canonical
order, or (b) make each lib self-loading (use `if (-not (Get-Command X))
{ . path }` guards). Option (a) is simpler.

**Minimal acceptance criteria:**

- One canonical dot-source order helper.
- Static check in Test-Phase2A2 that both entry points use it.
- No regression in any existing test.

## ARCH-004 -- review/ -> lib/ root dependency

**Risk:** low. `orchestrator/lib/review/ReviewAdapter.ps1` and
`orchestrator/lib/review/ReviewSurface.ps1` dot-source
`orchestrator/lib/Redactor.ps1`. This is reuse by design (the
Phase 2A-1 redactor is the source of truth) but architecturally
the reverse direction would be cleaner: `lib/review/` should not
reach back into `lib/`.

**Why not in 2A-4:** the dependency direction is intentional --
the redactor is shared infrastructure. Inverting the dependency
would either duplicate redaction logic or move Redactor.ps1 into
`lib/review/`, both of which are bigger refactors than warranted by
the architectural smell.

**Suggested phase:** when `lib/review/` ever needs to grow to a
package boundary (Phase 2B or later), revisit. Until then, accept
the back-reference.

**Minimal acceptance criteria:**

- A documented "redactor is shared, review can use it" note in
  `docs/design/phase2a3_review_surface_hardening.md` (already
  implicit; explicit is better).

## REL-006 -- signal-conflict semantics

**Status:** already covered. `CompletionVerifier` already returns
`NEEDS_REVIEW_CONFLICT` when both `claude_completed.json` and
`claude_failed.json` are present (line 72-78). Phase 2A-4 P6 added
explicit branch tests for this path.

**Suggested phase:** if deeper semantics ever needed (priority of
older vs newer signal, or recovering from one signal during a
running iteration), revisit.

**Minimal acceptance criteria (current):** Test-CompletionVerifier
covers the both-signals branch. ✓

## REL-007 -- partial-state recovery semantics

**Risk:** low. The current state-machine treats partial state as
"resume safely". `Invoke-WaggleIteration.ps1`'s resume short-circuit
(after Phase 2A-4 REL-003 lock fix) only short-circuits on terminal
state. Mid-state resumes re-run.

**Why not in 2A-4:** "partial-state recovery" is a design space, not
a single bug. Each scenario (state.json missing, signals dir present
without state.json, artifacts present without state.json, etc.)
needs a distinct decision. Phase 2A-4 P7 fixed the main race; the
remaining edge cases are research-grade.

**Suggested phase:** Phase 2A-5 or later, after one or two real
operator sessions surface the actual partial states that occur.

**Minimal acceptance criteria:**

- Inventory of partial-state cases observed in real iterations.
- A decision matrix per case (resume / restart / fail).
- Tests for each decision.

## REL-008 -- idempotency semantics

**Risk:** low-medium. Re-running with the same iteration_id (e.g.
operator passes `-IterationId X` twice) currently overwrites
state.json and re-runs. This is fine but not formally documented
or tested as "idempotent" or "destructive".

**Why not in 2A-4:** clear-cut documentation work. Either we declare
"same-id re-run is destructive; use -Force or -Resume" or we add
an explicit refuse-when-state-present guard. The Phase 2A-4 master
prompt says to keep this behavior unchanged.

**Suggested phase:** Phase 2A-5 or later, paired with REL-007.

**Minimal acceptance criteria:**

- Decision recorded in CLAUDE.md or README.
- Test asserting the documented behavior.

## Cross-cutting note: Invoke-WaggleReviewSubprocess remains separate

Phase 2A-4 master prompt rule 3 explicitly preserves
`Invoke-WaggleReviewSubprocess` as a Phase 2A-2 special-case. Phase
2A-4 fixed REL-005 (timeout enforcement) in place via bounded
`task.Wait()` rather than refactoring. This is intentional. ARCH-002
(consolidation) is the architectural follow-up.
