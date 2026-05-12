# ADR-056 — GC tuning per profile

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-055 (profile-aware budgets)

## Context

Python's gen-2 GC pauses can spike tail latency. On the FACTORY profile (4 GB working set, 16 concurrent replays), gen-2 collection can introduce 50-200ms p99 spikes during the request path. The 50-leaps menu (L39) calls for **GC tuning per profile**: pin gen-2 collection to off-peak windows on large profile.

## Decision

Per-profile `gc_config` controls Python `gc` module:

| Profile | gen-2 threshold | off-peak window | force collect |
|---|---|---|---|
| GADGET | default 700/10/10 | off | every request boundary |
| COTTAGE | 1500/20/20 | off | every 100 requests |
| HOME | 3000/30/30 | 02:00-04:00 local | hourly during peak suppressed |
| FACTORY | 5000/50/50 | 02:00-04:00 local | hourly + nightly gen-2 forced |

Off-peak window: gen-2 collection suppressed during peak hours via `gc.disable()` for gen-2 + manual collect during window.

## Invariants (GTP-001..GTP-007)

1. **Profile-specific gen-2 thresholds**: pinned in contract.
2. **Off-peak window respected on HOME/FACTORY**: gen-2 collection suppressed during peak hours.
3. **Manual collect during window**: nightly forced gen-2 at 02:00 local.
4. **GADGET preserves Python defaults**: no risk of changing small-profile behavior.
5. **Thresholds tuned by profile size**: larger profile → larger threshold (matches PAB-002 monotonicity).
6. **Telemetry on collect**: pause duration logged at gen-2 collect events.
7. **Disable+enable atomic**: gen-2 suppression and re-enable use lock to prevent race.

Contract: `docs/eig2/contracts/gc_tuning_per_profile.json`. Tests: `tests/contracts/test_gc_tuning_per_profile.py`.
