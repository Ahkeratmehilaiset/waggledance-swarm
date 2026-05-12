# ADR-034 — Anti-cargo-cult check for new solver promotion

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-024 (compact decision card), ADR-031 (confidence-bin gap mining), ADR-033 (failure-pattern mining)

## Context

Today's promotion gate validates a candidate against the same query distribution it was trained on. A solver that **memorized** training queries will pass validation but FAIL on novel inputs at production. The system has no defense against memorization disguised as competence — "cargo-cult" patterns that look correct but don't generalize.

The 50-leaps menu (L29) calls for an **anti-cargo-cult check**: a held-out adversarial probe set that a new solver must beat the baseline on, BEFORE promotion to canary.

## Decision

A new promotion gate, `AntiCargoCultGate`, inserted between candidate validation and canary promotion:

1. Maintains a set of `anti_pattern_probes` — adversarial paraphrases, edge-case inputs, distribution-shifted queries — stored at `configs/anti_cargo_cult_probes.yaml`.
2. On promotion-request, the candidate is evaluated against ALL probes in the set.
3. Promotion is BLOCKED if the candidate's anti-probe accuracy is below the baseline's anti-probe accuracy by more than `tolerance_pct=5%`.

The probe set is hand-curated initially (operator + Claude/Codex propose). Each probe has:
- input text + expected behavior class (not exact output — class)
- baseline (canonical solver) accuracy on this probe
- timestamp + author

Probes can be added but NOT silently removed: removing a probe requires an explicit YAML change with rationale comment.

## Consequences

### Promotion gate quality

* Solvers that memorized training queries are CAUGHT before canary, saving canary cycles for genuine generalizers.
* Operators get a stable adversarial regression suite that grows over time.

### Operational

* New probes can be added when an anti-pattern is discovered (e.g., a production miss reveals a class of edge cases).
* Probe-set rotation: stale probes (no failures in 90 days from any candidate) are tagged `stable` and de-prioritized in the gate run order (still run, but later).

### Storage

* Probe YAML ≈ 100-500 entries × ~500 bytes = ~250 KB. Version-controlled.

## Invariants

Pinned in `docs/eig2/contracts/anti_cargo_cult_check.json` and verified by `tests/contracts/test_anti_cargo_cult_check.py`.

1. **Probe set is YAML-pinned.** Probes live at `configs/anti_cargo_cult_probes.yaml`. Loaded once at gate-init.
2. **Required fields per probe.** input, expected_class, baseline_accuracy, added_at_utc, added_by.
3. **Tolerance bound.** `tolerance_pct` default 5%. Candidate must beat (baseline − tolerance). Tunable in [0, 20].
4. **Block on tolerance fail.** Promotion is BLOCKED (not just penalized) when candidate falls below baseline − tolerance.
5. **No silent probe removal.** Removing a probe requires explicit YAML diff with `removed_reason` comment. Implementation has no auto-prune for entries marked active.
6. **Stable-tag for stale probes.** Probes with no candidate failures in 90 days get `stable: true` tag. Tagged probes still run but deprioritized in order.
7. **Probe versioning.** Each probe has `probe_id` + `probe_version`. Editing a probe in place increments version; probe_id stable for tracking over time.

## Out of scope (this ADR)

* Implementation of `AntiCargoCultGate` — separate PR.
* Probe-generation tooling (synthetic adversarial paraphrases) — future ADR.
* Cross-domain probe sharing (one solver's probes used by another) — future ADR.

## References

* ADR-024 (compact decision card, promotion artifact precedent)
* ADR-031 (confidence-bin gap mining, candidate-generation context)
* ADR-033 (failure-pattern mining, complementary anti-knowledge)
* 50-leaps menu: L29 (this), L21, L22, L25, L24 (sleep consolidation)
