<!-- SPDX-License-Identifier: BUSL-1.1 -->
# P4b — Post-Merge Canary (V1)

**Status:** DRAFT spec (design-first). Implements RFC item **P4b** of the P4
Safety Substrate (`docs/architecture/P4_SAFETY_SUBSTRATE_RFC.md`, merged #1385).
Producer-authored (`fable-5`) per the tools delegation 2026-06-25; dual-RCO
review; gate/ops-adjacent → operator-sign. **Changes no runtime behavior** and
**activates nothing**: P4b is one input to the operator-gated, P4-gated P1
activation, which remains separately signed. Answers RFC §5 open questions for P4b.

## 0. One-paragraph summary

After a merge to `main`, the canary runs a **fast, bounded verification** (a canary
subset of the suite + key smoke/contract checks) and classifies the merge
**pass / regress**. On a **debounced, high-confidence** regress it emits a
structured **failure signal** that P4a (auto-rollback) consumes. The canary is
**observability-only**: it NEVER gates a merge (CI remains the authoritative
pre-merge gate) — it is the post-merge MTTR trigger. It is **fail-SAFE on the
destructive side**: uncertainty, flakiness, or an unrunnable canary **never** emits
a rollback trigger (a false trigger could revert a GOOD merge); it escalates to
operator/RCO awareness instead. Its **false-positive rate is quantified** against a
shared corpus and bounds the debounce.

## 1. Scope & non-goals

**In scope:** observe a freshly-merged `main` commit; run the canary set; classify;
emit a debounced regress signal for P4a; record a MAGMA canary receipt.

**Non-goals (hard):**
- **Not a pre-merge gate.** CI is the authoritative pre-merge gate; P4b runs only
  AFTER merge and never blocks a merge. (Acceptance bar, RFC P4b.)
- **Not a CI replacement.** It is a fast subset for MTTR, not full coverage.
- **Does not roll back.** P4b only *signals*; P4a *acts* (the constrained
  pure-revert-to-known-green fast-path). Separation of observe vs act is deliberate.
- **Does not act on non-reversible effects** (releases/tags/Docker-stable — Rule-10
  stays operator).

## 2. Canary set composition (RFC §5 answer)

The canary set is a **fixed, declared manifest** (not "whatever changed"), versioned
in-repo so a run is reproducible and the FP-rate is meaningful:

- **(a) Fast smoke** — process/import boot, health endpoints, bridge read/write
  round-trip — the "is main fundamentally alive" layer.
- **(b) Key contract/invariant checks** — the gate-truth invariants (consensus
  verdict determinism, charter classification on a fixed fixture, MAGMA receipt
  re-derivability) + any test tagged `@canary` (an explicit, reviewed allowlist —
  never an implicit glob).
- **(c) Bounded error-rate observation** — a short post-merge window sampling the
  primary runtime error/exception rate vs a rolling baseline.

The manifest is **declared** (a `canary_manifest`), so adding a check is a reviewed
change, and the set's runtime is predictable for the budget below.

## 3. Bounded time/resource budget (RFC §5 answer)

- **Wall-clock cap: ≤ 5 minutes** per canary run (hard timeout). A run that exceeds
  the cap is classified **INCONCLUSIVE**, not regress (fail-safe; see §6).
- **Single-in-flight:** at most one canary per merged SHA; a newer merge supersedes
  an in-flight canary for an older SHA (no stacking — mirrors the P0b loop guard).
- **Resource cap:** the canary runs in the existing CI/runner budget lane; it must
  not contend with the pre-merge CI gate (lower priority).

## 4. Debounced, high-confidence signal (RFC §5 + rco-1 2026-06-24)

P4a must **never** fire on a single (possibly flaky) canary run. P4b emits a
rollback-eligible signal **only** when a regress is **confirmed**:

- **N-of-M confirmation:** a regress must reproduce on **≥2 independent canary runs
  of the same merged SHA** (re-run on the same head), with the **same failing
  check(s)**. A regress seen once → `SUSPECT` (logged, not a P4a trigger).
- **Floor:** `required_confirmations ≥ 2` (no caller may lower it below 2 — aligns
  with the P4a debounce floor, #1389).
- **Same-cause binding:** the confirmations must share the same failing check id
  (two *different* flaky failures are not a confirmation).
- Only a `CONFIRMED_REGRESS` emits the P4a trigger.

## 5. Quantified false-positive rate (RFC §5 answer)

- **Definition:** FP-rate = P(canary classifies `CONFIRMED_REGRESS` | the merge is
  actually good), measured by replaying a **labeled corpus** of known-good merges +
  seeded-bad merges through the canary.
- **Shared corpus:** reuse the P4c adversarial-corpus / §-shared-fixture base
  (rco-1/rco-2, `wd-p4c-…`) — one fixture base, P4b's lens = "FP-rate on good
  merges + detection-rate on seeded regressions."
- **Acceptance threshold:** **FP-rate ≤ 1%** on the corpus AND **detection-rate
  ≥ 95%** on seeded regressions, BOTH after the §4 debounce. The FP-rate is
  reported in every canary receipt and re-measured when the manifest changes.
- A canary whose measured FP-rate exceeds the threshold is **not P4a-eligible**
  (it may still observe/alert, but cannot trigger auto-rollback) until tuned.

## 6. Fail-safe classification (the destructive-side asymmetry)

Three outcomes, with deliberate asymmetry (a false rollback is worse than a missed
auto-rollback, because a human MTTR still backstops a miss):

| outcome | when | action |
|---|---|---|
| `PASS` | canary set green within budget | record receipt; no signal |
| `CONFIRMED_REGRESS` | §4 debounced, same-cause, FP-rate within threshold | emit P4a trigger + receipt |
| `INCONCLUSIVE` | timeout / can't-run / flaky / single-run suspect / FP-rate over threshold | **NO P4a trigger**; escalate to operator/RCO awareness + receipt |

> Fail-safe rule: **uncertainty never triggers a rollback.** Only a positively
> confirmed, high-confidence, within-FP-budget regress acts. Everything else
> surfaces for human MTTR. (This is the opposite polarity from the pre-merge gate,
> which fails CLOSED to block; here the *rollback action* fails safe to NOT act on
> noise, because acting wrongly reverts good work.)

## 7. Signal interface to P4a (structured, re-derivable)

P4b emits a **structured** signal (no free-text authority — consistent with the
P2/D5 taxonomy): `{merged_sha, prior_good_sha, failing_check_ids[], confirmations,
fp_rate_at_emit, canary_manifest_version, receipt_ref}`. P4a independently
re-verifies eligibility (its pure-revert-to-known-green tree-equality proof, #1389)
— it does **not** trust a bare P4b flag. The two stay decoupled: P4b observes +
signals; P4a verifies + acts; both emit re-derivable MAGMA receipts.

## 8. MAGMA canary receipt

Every run emits a receipt: `merged_sha`, outcome, canary_manifest_version, per-check
results, run count / confirmations, measured FP-rate, time-budget used, and (on
`CONFIRMED_REGRESS`) the emitted P4a-trigger reference. A consumer must be able to
re-derive the outcome from the receipt (no trusting a bare classification).

## 9. Acceptance bar (matches RFC P4b)

1. A deterministic test seeds a regression into a sandbox merge → the canary
   classifies `CONFIRMED_REGRESS` within the §3 budget after §4 debounce, and emits
   the P4a signal + receipt.
2. A known-good merge replayed N times → **no** `CONFIRMED_REGRESS` (FP-rate within
   §5 threshold).
3. A timeout / unrunnable canary → `INCONCLUSIVE`, **no** P4a trigger, operator
   escalation (fail-safe).
4. Observability-only proven: the canary path has **no** pre-merge gate hook — it
   cannot block a merge.

## 10. Rollout & ownership

- **#1 this spec** (fable-5; dual-RCO; operator-sign — gate/ops-adjacent).
- **#2 canary runner + shared FP-corpus harness** (tools/lead; off-allowlist →
  operator-sign), DORMANT until P4-readiness wiring.
- **#3** consumed only by the operator-signed, P4-gated **P1 #3 activation**, which
  verifies the composite P4-ready signal (P4a test green + P4b canary operational +
  P4c corpus at maturity bar). **Nothing activates until then.**
- **Non-loosening:** P4b adds a post-merge observer + a fail-safe rollback *input*;
  it removes no gate, grants no authority, and never auto-acts destructively on
  uncertainty.
