# ADR-045 — Trust-staged routing

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-039 (multi-cell portfolio), ADR-037 (temporal trust decay)

## Context

Today, routing fan-out is uniform regardless of trust signals: a high-trust query (clear intent, trusted agents available) gets the same candidate set as a low-trust query (ambiguous intent, untrusted agents). The 50-leaps menu (L8) calls for **trust-staged routing**: high-trust queries take direct route (low overhead, K=1), low-trust queries get broader candidate set + cross-check (K=portfolio).

## Decision

Routing stage is derived from query+context trust:

| Stage | Trust range | Candidate K | Cross-check |
|---|---|---|---|
| `direct` | composite ≥ 0.80 | 1 | no |
| `broad` | 0.50 ≤ composite < 0.80 | 3 (per ADR-039) | no |
| `verified` | composite < 0.50 | 5 | yes (verifier agent) |

Stage is determined at `select_origin_cells_top_k` time based on the composite trust score of the BEST candidate after first pass.

## Invariants (TSR-001..TSR-007)

1. **Stage enum**: `{direct, broad, verified}` only.
2. **Boundaries pinned**: 0.80 and 0.50 thresholds in contract.
3. **K mapping**: direct=1, broad=3, verified=5.
4. **Verified cross-check**: only `verified` stage invokes verifier agent; others skip.
5. **No silent escalation**: stage upgrade (e.g., broad→verified) requires explicit caller signal (mirror RTB-007 from ADR-027).
6. **Profile override**: thresholds operator-tunable per profile, but K mapping fixed.
7. **Stage logged in trace**: every routing decision logs its stage for observability.

Contract: `docs/eig2/contracts/trust_staged_routing.json`. Tests: `tests/contracts/test_trust_staged_routing.py`.
