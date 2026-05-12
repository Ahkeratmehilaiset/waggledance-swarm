# ADR-035 — Stability score (Phase 2 trust signal)

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-021 (progressive replay), Codex's PR #287 (L41-L44 trust signals from AgentResult.metadata)

## Context

PR #287 (Codex's L41-L44) wired the **extraction** path for four Phase 2 trust signals (hallucination_rate, consensus_agreement, correction_rate, fact_production_rate). The TrustSignals dataclass in `waggledance/core/domain/trust_score.py` has a `freshness_score` field already populated from scheduler timestamps.

What is still missing: **stability_score** — the VARIANCE of an agent's answers to the same/similar query over time. A high-stability agent gives consistent answers to consistent inputs; a drifting agent gives different answers. Variance is detectable per-agent, not just fleet-wide.

The 50-leaps menu (L45) calls for `stability_score`: lower variance → higher stability → higher trust component. Detect drift / regression at INDIVIDUAL-agent granularity.

## Decision

`stability_score` is added to `TrustSignals` (extending the existing 6-signal dataclass — L51 contract test in PR #284 already validates the existing field set; this ADR pins the EIGHTH field).

```python
stability_score: float  # 0.0-1.0, higher is more stable
```

Computation, derived per-agent over a rolling 7-day window:

1. Sample N=20 (query, response) pairs where the query has been seen multiple times.
2. For each query-cluster, compute response-pair similarity (cosine on embeddings, or sentence-level token overlap, depending on solver type).
3. `stability_score = mean(similarity across all clusters)`, mapped to [0, 1] (1 = identical responses across time, 0 = unrelated responses).
4. When < 5 query-clusters with ≥ 2 responses exist, return 0.5 (uncertain — neither stable nor unstable).

## Consequences

### TrustSignals dataclass

* Field added: `stability_score: float`. This is the 7th field (existing 6 + new). PR #284 L51 contract test in `tests/contracts/test_fan_in_public_surface_contracts.py` MUST be updated to include this in the autonomy field list.
* Backward compat: a None or absent value defaults to 0.5 (uncertain).

### Composite trust score formula

* Stability adds a TERM to the composite. Initial weight: `stability_weight=0.10`. Operator-tunable.
* High-variance agents (drifting) get reduced composite trust → routing prefers stable agents for the same domain.

### Operational

* Computation cost: ~10 embedding-similarity ops per agent per 7-day window. Cheap.
* Cache the result in `AutonomyMetrics.stability_cache`, refresh every 24h.

## Invariants

Pinned in `docs/eig2/contracts/stability_score_trust_signal.json` and verified by `tests/contracts/test_stability_score_trust_signal.py`.

1. **Field name `stability_score`.** Exact name in the TrustSignals dataclass. Type `float`. Range [0.0, 1.0].
2. **Higher = more stable.** Convention: 1.0 = identical responses across time, 0.0 = uncorrelated. Lower-better convention (correction_rate, hallucination_rate) does NOT apply here.
3. **Rolling 7-day window.** Computed over last 7 days of agent activity. Configurable but pinned default.
4. **Default 0.5 on insufficient data.** When < 5 query-clusters with ≥ 2 responses, return 0.5 (neutral). Avoids false-confident high or low scores at fleet warm-up.
5. **Refresh cadence 24h.** Stability is cached and refreshed every 24h, not per-query. Bounded compute.
6. **L51 contract test updated.** When this field is added to TrustSignals, the L51 contract test in `tests/contracts/test_fan_in_public_surface_contracts.py` MUST be amended to include `stability_score` in the autonomy field list. Implementation PR is responsible.
7. **Backward-compat default in metadata extraction.** If `AgentResult.metadata` lacks `stability_score`, the extractor returns 0.5 (matching INV-4 sufficient-data threshold).

## Out of scope (this ADR)

* Implementation of `StabilityComputer` — separate PR.
* Embedding-similarity vs token-overlap choice — implementation detail, picked per solver type.
* Cross-agent stability comparison (operator dashboards) — separate PR.

## References

* PR #287 (Codex's L41-L44 trust signal extraction)
* `waggledance/core/domain/trust_score.py` (TrustSignals dataclass)
* PR #284 (L51 fan-in contract test, MUST be updated when this field added)
* 50-leaps menu: L45 (this), L41-L44 (Phase 2 first signals), L47 (latency_consistency), L49 (temporal decay)
