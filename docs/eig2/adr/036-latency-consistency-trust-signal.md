# ADR-036 — Latency consistency (Phase 2 trust signal)

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: PR #287 (L41-L44 trust signal extraction), ADR-035 (stability_score)

## Context

`validation_rate` and `freshness_score` (existing TrustSignals fields) capture WHETHER an agent succeeds and WHEN it last succeeded. Neither captures HOW PREDICTABLY it succeeds — the spread of its response latencies.

A solver with p50=20ms / p99=25ms is more trustworthy than one with p50=20ms / p99=2000ms even if both have the same validation rate. The 99th-percentile tail amplifies cascade failure risk: every downstream caller pays the worst-case wait.

The 50-leaps menu (L47) proposes `latency_consistency`: ratio p50/p99 (or its inverse). Lower tail amplification → higher consistency → higher trust component.

## Decision

`latency_consistency` is added to `TrustSignals` as the 8th field (after `stability_score`'s ADR-035 7th field).

```python
latency_consistency: float  # 0.0-1.0, higher is more consistent
```

Computation:

1. Sample agent's last N=100 response latencies (per `AgentResult.latency_ms`) over the rolling 24-hour window.
2. Compute `p50_ms` and `p99_ms`.
3. `latency_consistency = clamp(p50_ms / p99_ms, 0.0, 1.0)`. p50=p99 → 1.0 (perfectly consistent). p99=10×p50 → 0.1.
4. < 20 samples → return 0.5 (neutral / insufficient data).

## Consequences

### TrustSignals dataclass

* Field added (8th, after stability_score).
* L51 contract test MUST be updated when this field lands.

### Composite trust score formula

* Adds `latency_consistency * latency_weight=0.10` term.
* High-tail-amp agents → lower composite → routing prefers consistent agents.

### Operational

* Computation cost: sort 100 latencies, two percentile lookups. ~10 µs per refresh. Cached 1h.

## Invariants

Pinned in `docs/eig2/contracts/latency_consistency_trust_signal.json` and verified by `tests/contracts/test_latency_consistency_trust_signal.py`.

1. **Field name `latency_consistency`.** Type `float`. Range [0.0, 1.0].
2. **Higher = more consistent.** Convention `higher_is_better` matches `stability_score` (ADR-035).
3. **Sample size 100, window 24h.** Computed over last 100 responses within last 24h.
4. **p50 / p99 formula.** `latency_consistency = clamp(p50_ms / p99_ms, 0.0, 1.0)`. Pinned in contract.
5. **Default 0.5 on insufficient data.** < 20 samples → return 0.5.
6. **L51 contract test must update.** Same requirement as STB-006: implementation PR amends L51 fan-in contract test.
7. **Cache refresh 1h.** Faster than stability_score (24h) because latency drift can happen within a day.

## Out of scope (this ADR)

* Implementation of `LatencyConsistencyComputer` — separate PR.
* Per-domain latency baselines (factory queries naturally slower than cottage queries) — future ADR if measured benefit warrants.
* Alternative formula (e.g., (p50/p99)^0.5 for less aggressive penalty) — explicitly NOT swapped; if changed, new ADR.

## References

* PR #287 (Codex's L41-L44 trust signal extraction)
* ADR-035 (stability_score, companion Phase 2 signal)
* PR #284 (L51 fan-in contract test)
* 50-leaps menu: L47 (this), L41-L44 (existing Phase 2), L45 (stability), L49 (temporal decay)
