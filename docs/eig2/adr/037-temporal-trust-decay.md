# ADR-037 — Temporal trust decay

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: PR #287 (L41-L44, freshness_score precedent), ADR-035 (stability_score), ADR-036 (latency_consistency)

## Context

Today `freshness_score` (PR #287) decays linearly from 1.0 to 0.0 over a fixed 7-day window keyed to scheduler's `recent_successes` timestamp. This is **score-side** decay — freshness alone weakens, but the rest of the composite stays intact even if the agent has been inactive for a month.

The 50-leaps menu (L49) calls for **temporal trust decay at the composite level**: if an agent has no recent successes, its WHOLE trust composite erodes, not just freshness. Stale agents drop out of routing automatically because their composite falls below the trust threshold.

## Decision

Composite trust score gets a multiplicative decay factor applied AFTER the per-signal weighted sum:

```python
composite_raw = sum(signal_i * weight_i for i, _ in signals)
decay_factor = compute_temporal_decay(last_success_ts, half_life_days=7)
composite = composite_raw * decay_factor
```

Where `compute_temporal_decay`:

```python
def compute_temporal_decay(last_success_ts: float, half_life_days: float = 7.0) -> float:
    if last_success_ts <= 0:
        return 0.0  # never seen success -> full decay
    age_days = (time.time() - last_success_ts) / 86400
    if age_days <= 0:
        return 1.0
    # Exponential decay with half-life
    return 0.5 ** (age_days / half_life_days)
```

Half-life 7 days means a 14-day-stale agent retains 25% composite, 21-day-stale retains 12.5%, 28-day-stale retains 6.25%. Combined with a routing threshold of 0.30, the stale agent drops out around day 11.

## Consequences

### Routing

* Stale agents (no recent successes) auto-drop from routing candidate pool.
* Active agents are favored even when their per-signal numbers are slightly worse.
* Recovers from agent rehabilitation: a returning agent gets `decay_factor=1.0` after first recorded success.

### Operational

* New formula composes with existing weighted-sum trust; backward-compat preserved when `decay_factor=1.0` (no decay).
* Operator-tunable half-life per profile.

## Invariants

Pinned in `docs/eig2/contracts/temporal_trust_decay.json` and verified by `tests/contracts/test_temporal_trust_decay.py`.

1. **Multiplicative composition.** decay is applied as `composite = composite_raw * decay_factor`. NOT subtractive (would clamp at floor wrongly) or replacement (would erase other signals).
2. **Half-life 7 days.** Default pinned at 7 days. Operator-tunable in [1, 90].
3. **Exponential decay.** `decay = 0.5 ** (age_days / half_life_days)`. NOT linear, NOT step. Exponential gives smooth dropout.
4. **Never-seen -> 0.0.** `last_success_ts <= 0` means agent has never been seen succeeding. decay_factor=0.0 → composite=0.0 (agent not used by router).
5. **Future timestamp -> 1.0.** Clock skew edge case: `last_success_ts > now` → return 1.0 (clamp at no-decay). Prevents negative-age math errors.
6. **Recoverable.** First recorded success after staleness resets decay_factor to 1.0. No permanent demotion.
7. **Composes with freshness_score.** Per-signal `freshness_score` (PR #287) continues to exist as a SIGNAL; THIS decay applies to the COMPOSITE. The two are complementary, not redundant.

## Out of scope (this ADR)

* Implementation of `compute_temporal_decay` + composite formula update — separate PR.
* Per-domain half-life (factory vs cottage agents may have different idle expectations) — future ADR.
* Predictive recovery boost (if an agent shows signs of resumption) — future ADR.

## References

* PR #287 (Codex's L41-L44, freshness_score precedent)
* ADR-035 (stability_score)
* ADR-036 (latency_consistency)
* 50-leaps menu: L49 (this), L41-L44, L45, L47
