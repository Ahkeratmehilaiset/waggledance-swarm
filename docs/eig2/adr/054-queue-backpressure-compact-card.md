# ADR-054 — Queue + backpressure for compact-card writes

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-011 (compact-card write-storm breaker), ADR-014 (queue+backpressure framework), ADR-024 (compact card schema)

## Context

ADR-014 establishes the queue+backpressure framework for derived writes. ADR-024 (L12) pins the compact-card-v1 schema. The 50-leaps menu (L32) requires explicit wiring of L12's compact-card writes through ADR-014's queue.

Today, compact card writes have direct write-storm risk: a high-churn solver dispatch period (autogrowth bursts, batch evaluation) could write 1000+ cards in seconds, hitting H47-class blocking-IO risk on the async path.

## Decision

Compact-card writes go through a bounded `CompactCardWriteQueue`:

* Queue max size: `max_queue_size=10000` cards.
* Soft threshold: 7000 (70%) → emit slow-down telemetry.
* Hard threshold: 10000 → activate write-storm breaker per ADR-011: raw MAGMA stays authoritative; compact cards become async-best-effort.
* Drain rate: queue worker pulls batches of 100 cards every 100ms = 1000 cards/sec sustained.

Producer-side: every compact-card writer puts to queue (non-blocking). On hard threshold, put returns drop indicator; producer logs WARNING and continues without dropping the raw write.

## Invariants (QBC-001..QBC-007)

1. **Max queue 10000**: pinned default. Operator-tunable.
2. **Soft 70% threshold**: telemetry only, no behavior change.
3. **Hard 100% threshold**: breaker fires per ADR-011.
4. **Drain rate 1000 cards/sec**: batch=100, interval=100ms. Tunable.
5. **Non-blocking producer**: card write API never blocks request path.
6. **Raw MAGMA authoritative**: queue full → cards dropped, raw events still written. No data loss in raw chain.
7. **Backpressure metrics**: queue depth + drop_count emitted to events.jsonl every drain cycle.

Contract: `docs/eig2/contracts/queue_backpressure_compact_card.json`. Tests: `tests/contracts/test_queue_backpressure_compact_card.py`.
