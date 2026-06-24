<!-- SPDX-License-Identifier: BUSL-1.1 -->
# P1 — Proven-Safe Auto-Sign Class (V1)

**Status:** DRAFT spec. This document defines an INVARIANT the operator signs
**once** (auditable). It is the first of a 3-PR rollout (RFC item P1). **Nothing
in this spec loosens any gate until the separate gate-wiring PR (#3) is
operator-signed.** Authoring this spec (PR #1) and the dormant checker (PR #2)
changes no runtime behavior.

RFC: WD Bridge Throughput, Resilience & Pool-Decorrelation, item **P1**
("asymmetric operator-reduction: per-policy sign for a proven-safe class").

## 0. One-paragraph summary

P1 lets a pull request auto-merge **without the per-PR operator signature** —
**only** when a mechanically-proven, fail-closed checker certifies the PR is in a
narrow "proven-safe" class. The waiver removes **only the operator signature**.
It is **ANDed on top of the entire existing gate**: full build consensus
(lead + tools), recognized-RCO `RCO_PASS` at the exact head, no RCO veto, CI 6/6,
and charter-clean all still apply, unchanged. The operator signs the **invariant
below once**; the gate then enforces it fail-closed per-PR.

## 1. What P1 changes — and what it does NOT

**Changes (the only change):** for a PR the checker certifies IN-CLASS, the
autonomous-merge gate may proceed **without** waiting for a per-PR operator
signature, substituting the operator's **one-time signature on this invariant**.

**Unchanged — every other guarantee is preserved (non-loosening):**
- Build consensus: lead **and** tools `build_consensus_pass` at exact head.
- Recognized-RCO `RCO_PASS` at the exact head (dual-RCO where charter requires).
- **RCO veto stays absolute and per-identity** — any `finding`/`changes_requested`
  from a recognized RCO blocks, and a veto outranks a pass.
- `author != reviewer` (independence) stays.
- Head-exact binding stays — any content-changing re-push invalidates approvals.
- CI 6/6 green stays. Charter-clean stays.
- Silence still BLOCKS; absence of a required signal never default-allows.

P1 is **strictly additive scrutiny that the operator pre-authorizes** for a class
it can verify is harmless — it never removes build, RCO, CI, or charter checks.

## 2. In-class predicates (the checker must PROVE ALL, fail-closed)

A PR is IN-CLASS only if **every** predicate A–F holds. Any failure, any
exclusion, any parse error, or any ambiguity → **NOT in class** → per-PR operator
signature required (the pre-#1 behavior).

- **(A) Paths.** Every changed path is strictly within `tests/**`,
  `docs/runs/**`, or `docs/benchmarks/**`, **or** is an **additive metrics
  counter** (a new symbol only; no edit to an existing metric or default line).
- **(B) Effect.** Read-only **or** default-OFF: no change to a default-emission
  value, and no new throwable code on a live hot path.
- **(C) No `claim_safe` flip.** The change must not flip any `claim_safe` (or
  equivalent capability-claim) value.
- **(D) No authority-flag edit.** No edit to authority flags
  (`gate_skip` / `solver_call` / `receipt_required` / `clinical_decision`, etc.).
  Authority flags are read strict-bool (`x is True`) and never string-coerced.
- **(E) No control-plane / runtime change.** No routing, control-plane, or
  runtime-behavior change.
- **(F) Hard exclusions (any one → operator sign).** The PR touches none of:
  gate / charter / denylist logic; `.agent-bridge/bin/**`; `.github/workflows/**`;
  `requirements*` / lockfiles; `AGENTS.md` / `CLAUDE.md` / tracked master-prompts;
  any Rule-10 surface; anything `evaluate_paths` denylists or
  `evaluate_diff_content` flags.

### Fail-closed rule
> Any path outside (A); any (C)/(D)/(E) pattern; any (F) exclusion; any parse
> error or ambiguity → the PR is **NOT in class** → per-PR operator signature is
> required. The checker **never** default-allows on uncertainty.

## 3. Anti-widening controls

- The checker file `tools/check_proven_safe_autosign_class.py` is **added to the
  charter denylist** (PR #3). The class therefore **cannot be silently widened**
  by editing the checker — any change to it is itself operator-gated.
- Each P1 auto-merge emits a **MAGMA receipt** re-deriving the A–F verdict and
  recording the **signed-invariant version** of this spec. A consumer must be
  able to re-derive the in-class verdict from the receipt (no trusting a bare
  flag).
- **Widening the class** (relaxing any predicate, adding a path root) requires a
  **future operator-signed amendment** to this spec (a new version), reviewed by
  both recognized RCOs.

## 4. Rollout — 3 separate PRs (never bundled)

1. **PR #1 — this spec.** `docs/architecture/P1_PROVEN_SAFE_AUTOSIGN_CLASS_V1.md`.
   Off-allowlist; the **operator signs the invariant** (§1–§3). Changes no
   behavior.
2. **PR #2 — checker + tests, DORMANT/UNWIRED.**
   `tools/check_proven_safe_autosign_class.py` implementing A–F fail-closed, with
   a positive corpus (#1364/#1369-shaped in-class) and a negative corpus (one
   case per F exclusion and per C/D/E violation, each proving fallback to
   operator-sign). It is consulted by **nothing** — pure, testable logic.
3. **PR #3 — denylist entry + gate-wiring.** Adds the checker to the charter
   denylist and wires the gate to consult it to waive **only** the operator
   signature. **Gate-critical / denylisted → a SEPARATE operator sign at exact
   head, highest scrutiny.** **Nothing activates until PR #3 is operator-signed.**

## 5. Activation prerequisites (PR #3 only)

Before the loosening in PR #3 may activate:
1. **Separate operator signature** at the exact head of PR #3 (distinct from the
   §1 invariant signature).
2. **P4 safety substrate as prerequisite** — per CLAUDE.md Rule 10 and the
   dual-RCO fence (rco-1 2026-06-24): any gate loosening is gated on a matured
   synthetic adversarial corpus + a proven auto-rollback test + a post-cutover
   verification harness. **P1 activation (PR #3) must not precede P4.** PRs #1
   and #2 (this spec + the dormant checker) carry no such dependency because they
   loosen nothing.
3. **Corpora green** — the tools-run positive and negative corpora pass,
   demonstrating in-class auto-sign and out-of-class fallback to operator-sign.

## 6. Ownership & separation of duties

- **fable-5 (non-RCO producer)** authors PR #1 (this spec) and PR #2 (checker).
  A recognized RCO must **not** author P1 — it would collapse dual-RCO to a
  single independent reviewer on the most safety-critical class, and is
  reviewer-designing-the-reviewer-rules (rco-1 + rco-2, 2026-06-24).
- **codex-tools-1** build-signs and runs the positive/negative corpora. (Tools
  cannot author — a tools-authored PR self-cosign-blocks the tools build slot and
  there is no tools-slot waiver.)
- **claude-rco-1 / claude-rco-2** fence the checker adversarially
  (negated/malformed/boundary inputs per predicate) and review every P1 PR for
  gate-loosening; their veto is absolute.
- **codex-lead-1** authors PR #3 (gate-wiring) and coordinates.
- **Operator** signs the §1 invariant (PR #1) and, separately, the activation
  (PR #3).

## 7. Relationship to CLAUDE.md

P1 modifies the autonomous-**merge** approval surface (it pre-authorizes the
operator-signature for a proven class). PR #1/#2 are off-allowlist
documentation/tooling that change nothing live. PR #3 (activation) is
Rule-10-adjacent (a gate loosening) and is therefore operator-signed and
P4-gated. P1 does not alter the bridge-consensus contract (Rule 9a) except to add
the operator-signature waiver for the mechanically-proven in-class set; build
consensus, recognized-RCO pass, RCO veto, author≠reviewer, head-exact binding,
CI, and charter-clean are all retained unchanged.
