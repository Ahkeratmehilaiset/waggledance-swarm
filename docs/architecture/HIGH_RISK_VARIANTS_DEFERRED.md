# High-Risk Variants — Deferred Topics

**Status:** Deferred. None of the topics on this page are implemented
in this branch. They are documented here so a future session has a
clear, blocker-aware starting point and does not silently rediscover
the same risks.

The Cross-Capsule Observer (Phase 9 §Q) is the only piece of §Q that
ships in this branch. It is built to be safe today: redacted summaries
in, advisory pattern records out, no raw data leakage.

The variants below extend §Q (and adjacent phases). Each lists:
- what it would do
- why it is risky enough to defer
- the explicit blockers that must be cleared before any implementation

## 1. Speculative parallel provider ensembles

**What:** Issue the same consultation to multiple providers in
parallel, then arbitrate the responses with an internal scorer.

**Risk:**
- amplifies API spend non-linearly
- may obscure provenance (who actually said what?)
- the scorer becomes a hidden authority surface that no human
  approved to author calibration claims

**Blockers:**
- per-task budget caps that include parallel-fanout multipliers
- provenance retained per arm; no merged answer without human review
- a profile flag (`experimental.parallel_ensemble`) that defaults off
  and is only toggled by an explicit human-gated record

## 2. Predictive cache preheating

**What:** Predict which provider consultations will be needed in the
near future and pre-issue them.

**Risk:**
- silent budget consumption with no concrete user request behind it
- a wrong predictor inflates spend and pollutes the consultation log
- a right predictor reduces opportunity to notice usage drift

**Blockers:**
- per-window cap on speculative-only spend
- predictor accuracy must be measured against a separate baseline,
  not against its own historical hits
- speculative entries must be tagged in the consultation log so they
  can be excluded from any distillation corpus

## 3. No-human-in-loop micro-learning expansions

**What:** Allow micro-learning lane updates beyond the bounded
priority adjustments defined in Phase 9 §F (see
`micro_learning_lane.py`).

**Risk:**
- expanded micro-learning is the single fastest path to silent drift
- without strict bounds, it can effectively rewrite policy behavior
  one nudge at a time

**Blockers:**
- formal proof that an expanded operation cannot violate any
  constitution.yaml hard rule
- a rollback story that returns to the exact pre-expansion priority
  state byte-for-byte
- per-tick volume cap and a circuit breaker that quarantines the lane
  on suspicious aggregate movement

## 4. Limited canary auto-promotion under experimental profile

**What:** Allow a subset of low-risk capabilities to auto-promote
through the canary stage of the promotion ladder (Phase 9 §M) without
explicit per-promotion human approval.

**Risk:**
- the promotion ladder's guarantee is "no_runtime_auto_promotion";
  this variant directly weakens it
- the definition of "low-risk" is itself a promotion-class question

**Blockers:**
- a separate experimental profile must opt in explicitly
- "low-risk" must be a fixed allow-list, not a heuristic
- aggregate canary metrics gate before any auto-progression
- any auto-promotion still emits a record on the human review queue
  for retroactive ack within N hours

## 5. Advanced local model escalation

**What:** Escalate a local model from `advisory` toward main-branch
authorship (today blocked by Phase 9 §N's safe routing contract).

**Risk:**
- this is the headline opacity risk: a local model whose training
  corpus was itself shaped by its own outputs

**Blockers:**
- per-domain corpus pinning with externally verifiable hashes
- continuous drift detector (Phase 9 §N) at `nominal` for a fixed
  observation window
- explicit human review on the promotion ladder for each task_kind
  expansion
- documented rollback to `shadow_only` if drift severity moves to
  `watch` or higher

## 6. Generative memory compression — deferred to Phase 12+

**What:** Replace stored memory entries with model-generated summaries
that materially reshape semantics rather than just rephrase.

**Risk:**
- fundamentally violates "the memory is the record"
- once compressed away, the original is gone

**Blockers:**
- two-tier storage (compressed + raw retained) with retention proofs
- byte-stable round-trip tests for any round-trip-claimed compression
- explicit separation from Phase 9: this is Phase 12+ territory and
  MUST NOT be reattempted under §Q

---

## Process rule

If a future session implements any of the above:

1. it MUST first remove the topic from this file
2. it MUST extend the relevant phase doc (e.g. §M, §N, §F) with the
   concrete safety contract
3. it MUST land tests that verify the contract via source-grep AND
   behavioral invariants

A future session is not allowed to "soft-enable" any of these by
slipping a feature flag in without doing 1-3 first.
