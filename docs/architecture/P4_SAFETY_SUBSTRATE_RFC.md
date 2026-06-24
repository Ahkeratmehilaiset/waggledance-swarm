<!-- SPDX-License-Identifier: BUSL-1.1 -->
# RFC: P4 — Safety Substrate (auto-rollback + post-merge canary + matured adversarial corpus)

**Status:** Draft (for swarm + operator review). Design-first, mirroring the P1 spec.
**Author:** codex-lead-1 (lead-impl).
**Date:** 2026-06-24
**RFC parent:** WD Bridge Throughput, Resilience & Pool-Decorrelation (#1370), item **P4**.
**Relates to:** `P1_PROVEN_SAFE_AUTOSIGN_CLASS_V1.md` (P4 is the HARD prerequisite for P1
activation, per the operator ruling 2026-06-24), and CLAUDE.md **Rule 10** (P4 builds
Rule-10's three named preconditions). **This RFC designs P4; it does NOT execute the
Rule-10 Stage-2 atomic-flip cutover** (that remains operator-signed under Rule 10).

## 0. Why P4, why now
P1 (#1383 spec + #1384 checker) merged but is **dormant**: it activates nothing until the
P1 **#3 gate-wiring** is operator-signed, and the operator ruled #3 activation **must not
precede P4**. So P4 is the critical path to P1 delivering any operator-reduction. More
broadly, P4 shifts reversible changes from *prevention-first* to **fast-recovery-first
(optimize MTTR, not just MTBF)** — the substrate that eventually lets even off-allowlist
changes flow with a rollback safety net.

## 1. Scope — three components (all must reach their bar before P4 is "ready")

### P4a — Auto-rollback mechanism + a PROVEN auto-rollback test
- **What:** on a defined post-merge failure signal (see P4b), automatically revert the
  offending merge (a `git revert`-style reversing PR, or a fast-forward to the prior
  known-good SHA) — never a force-push, never history rewrite.
- **Acceptance bar:** a deterministic, repeatable TEST that (1) merges a deliberately-bad
  change into a sandbox branch, (2) fires the failure signal, (3) shows the auto-rollback
  reverts to the prior good SHA, (4) emits a MAGMA rollback receipt (offending SHA, good
  SHA, trigger, actor). Fail-closed: if the rollback cannot be proven to complete, it
  escalates to operator, never leaves a half-rolled-back state.
- **Rollback-path is itself a constrained fast-path (rco-1 2026-06-24):** auto-merging a
  rollback must be a PURE revert to a prior **known-green** SHA only — mechanically verified
  that the resulting tree equals a previously **CI-green + consensus-approved** state — never
  an arbitrary edit. A rollback carrying any new content would be an unreviewed auto-merge
  and is forbidden; such a case escalates to operator. The rollback emits a MAGMA receipt
  re-deriving the tree-equality proof.
- **Non-goals:** does not roll back non-reversible effects (releases/tags/Docker-stable —
  Rule-stays-operator); rollback applies to ordinary mergeable commits only.

### P4b — Post-merge canary / post-cutover verification harness
- **What:** after a merge to `main`, run a fast verification (canary subset of the suite +
  key smoke/contract checks) and classify pass/regress; on regress, emit the failure
  signal that P4a consumes.
- **Acceptance bar:** the canary reliably catches a seeded regression within a bounded time
  budget, with a quantified false-positive rate; it is **additive/observability-only**
  (it never gates a merge by itself — CI remains the authoritative pre-merge gate) and is
  the post-merge MTTR trigger, not a second pre-merge gate.
- **High-confidence/debounced signal before P4a fires (rco-1 2026-06-24):** P4a must NOT
  fire on a single (possibly flaky) canary run — require a debounced, high-confidence
  failure (e.g. a one-cycle confirmation / N-of-M), so a false positive cannot revert a
  GOOD merge (revert-thrash). The quantified false-positive rate (see §5) bounds this.
- **Non-goals:** not a replacement for CI; not a pre-merge gate.

### P4c — Matured synthetic adversarial corpus  *(RCO domain — do NOT naively append)*
- **What:** mature the existing gate-conformance / adversarial corpus to a defined
  **maturity bar** so the autonomy gates (incl. the P1 checker) are exercised against a
  comprehensive, deduplicated, distinct-case-validated adversarial set.
- **Acceptance bar (proposed; RCO to ratify):** (1) coverage of every demonstrated bypass
  class found to date (the P1 checker's 6 classes / 19 vectors are seed material:
  endswith-admission, charter=None, nested-call-in-args, _norm leading-dot, safe-root RCE,
  escape-hatch A+B+C reflection-dunder incl `__subclasses__`); (2) a validator that
  re-derives the expected verdict from each case's content (no hardcoded expectations) and
  rejects dropped/duplicate case_ids; (3) anchor files charter-protected so the corpus
  can't be silently weakened. **Owner: claude-rco-1 / claude-rco-2** — corpus design and
  the maturity bar are theirs; this RFC only records the dependency.

## 2. How P4-readiness gates P1 activation
The P1 **#3 gate-wiring** (lead-authored, separate operator sign) must, before it may
waive any operator signature, verify a **P4-ready signal**: P4a test green + P4b canary
operational + P4c corpus at its maturity bar, each recorded by a re-derivable MAGMA
receipt. Absent any of the three → #3 stays dormant; the gate keeps requiring the per-PR
operator signature. Fail-closed: P4-readiness is proven, never assumed.

## 3. Non-loosening invariants (unchanged across P4)
- P4 changes **recovery**, not the **approval** gate: build consensus (lead+tools),
  recognized-RCO `RCO_PASS`, the absolute RCO veto, author≠reviewer, head-exact binding,
  CI, and charter all remain exactly as today.
- **P4 does NOT execute the Rule-10 Stage-2 atomic-flip cutover.** Building the
  preconditions is not the cutover; the cutover stays one-shot operator-signed under
  Rule 10 / `STAGE2_CUTOVER_RFC.md`.
- Auto-rollback never force-pushes, never rewrites history, never touches
  release/tag/Docker-stable surfaces.
- Silence/ambiguity on any P4 signal BLOCKS P1 activation; it never default-allows.

## 4. Sequencing & ownership
1. **P4 design (this RFC)** — lead; review by RCOs + tools + fable; operator notes the plan.
2. **P4c corpus maturity bar** — RCO-defined first (it shapes P4a/P4b acceptance).
3. **P4a auto-rollback + test** and **P4b canary** — tools + lead, in parallel, each
   landing via the normal gate (gate/ops-adjacent pieces are denylist/off-allowlist →
   operator-sign).
4. **P1 #3 wiring** — lead authors once P4 components exist; checks the P4-ready signal;
   operator-signed + P4-gated; nothing activates until then.

## 5. Open questions (for review)
- P4b canary subset composition + time budget + acceptable false-positive rate?
- P4a trigger source: P4b only, or also operator/RCO manual + external monitors?
- P4c maturity bar exact thresholds (RCO to set).
- Where does the P4-ready signal live (charter field? a checked receipt set?) so the #3
  wiring can verify it fail-closed?

This RFC touches `docs/architecture/` only; it changes no runtime/gate code and activates
nothing. It parks for swarm review + operator priority.
