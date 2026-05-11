# ADR-014 - Queue and backpressure before breaker for EIG2 writes

Status: proposed
Author: Codex
Peer reviewer: Claude (signature pending)
Date: 2026-05-11
R-rule: R14

## Context

Circuit breakers are useful only after the system has a measured pressure
signal. A breaker without a queue/backpressure layer is a late binary failure:
normal traffic proceeds until it suddenly stops. EIG2 adds several optional or
derived write streams, so the system needs smooth degradation before hard stop.

## Decision

Every new EIG2 write path that is not the raw authoritative MAGMA append must be
structured as:

1. bounded queue,
2. explicit backpressure threshold,
3. metric/alarm emission,
4. breaker only after backpressure fails or budget is exhausted,
5. fail-closed behavior that preserves baseline/raw state.

This applies to compact-card writes, tunnel registry writes, MAGMA secondary
indices, swarm consensus logs, and future autonomy-growth write streams.

Breaker-only implementations are rejected.

## Alternatives considered

1. Breaker first. Rejected: hides pressure until a hard edge.
2. Retry loops without queue bounds. Rejected: increases p99 and can create
   write amplification.
3. Global queue shared by all EIG2 writers. Rejected for M3/M4; per-writer
   quotas are easier to audit and tie to Option-B tests.

## Consequences

- Each writer needs queue capacity, pressure metric, and breaker state.
- Contract tests can assert pressure behavior deterministically.
- Later runtime work can degrade optional features while preserving baseline.

## Safety impact

Positive. Optional EIG2 growth cannot silently overwhelm baseline storage.

## Performance impact

Positive under burst load; negligible at idle.

## MAGMA invariant impact

Positive. Raw MAGMA append remains the first-class write and derived writes can
be skipped.

## Audit / regression class

`bridge_classify.py` maps breaker-before-backpressure or write-without-
backpressure to `INVARIANT_BREAK`; storage pressure still maps to
`STORAGE_RESOURCE_ISSUE` for runtime incidents.

## Reviewed by other agent

Pending Claude peer review.

## Related tests

- `tests/orchestrator/test_bridge_classify.py::test_write_without_backpressure_detected_as_invariant_break`
- Future per-writer Option-B tests required by ADR-015.

## Provenance

Generalized from H47, PR #224, Option-B concurrency evidence, and the 200-option
convergent ship-list item 6.

## Sign-off

- Author (Codex): signed.
- Peer reviewer (Claude): pending.
