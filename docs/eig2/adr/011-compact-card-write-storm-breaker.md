# ADR-011 - Compact-card write-storm breaker

Status: proposed
Author: Codex
Peer reviewer: Claude (signature pending)
Date: 2026-05-11
R-rule: R11

## Context

EIG2 creates compact decision cards for audit-eligible events, but raw MAGMA
events remain authoritative. If every raw append synchronously writes a compact
card on the request path, card creation can become the new latency tail and can
neutralize the Option-B read-side wins.

The 200-option exercise converged on queue plus backpressure before breakers
for optional derived writes.

## Decision

Compact-card writes are derived, optional writes. They must never block raw
MAGMA append or hot routing.

Required behavior:

1. Raw MAGMA append is attempted first and remains authoritative.
2. Compact-card creation is queued with bounded capacity.
3. Backpressure starts before a circuit breaker trips.
4. If the queue is full, the compact card is skipped or delayed; raw MAGMA is
   preserved.
5. Breaker activation emits an alarm and bridge/MAGMA evidence.
6. Replay and hydration must tolerate missing compact cards by falling back to
   raw MAGMA.

## Alternatives considered

1. Synchronous compact-card writes. Rejected: increases p99 and makes derived
   data part of the hot path.
2. Drop raw events when compact-card write fails. Rejected: violates hard rule
   3 and MAGMA authority.
3. Unbounded queue. Rejected: converts latency failure into memory failure.

## Consequences

- Compact memory remains a performance hint, not a truth source.
- M3/M4 implementations need queue metrics and breaker tests.
- Backpressure thresholds become config-controlled instead of ad hoc.

## Safety impact

Strongly positive. Raw audit continuity is preserved under write pressure.

## Performance impact

Positive under load. Optional writes can degrade without blocking the request
path.

## MAGMA invariant impact

Positive. Raw append-only MAGMA remains authoritative.

## Audit / regression class

`bridge_classify.py` maps breaker-before-backpressure, unbounded write, or
write-without-backpressure language to `INVARIANT_BREAK`.

## Reviewed by other agent

Pending Claude peer review.

## Related tests

- `tests/orchestrator/test_bridge_classify.py::test_write_without_backpressure_detected_as_invariant_break`
- Planned M3 compact-card writer Option-B contract tests under `tests/contracts/`.

## Provenance

Derived from the convergent ship-list item "Put optional writes behind queue +
backpressure before breakers" and the combined insight that this pattern applies
to compact cards, tunnel registry writes, MAGMA secondary indices, and swarm
consensus logs.

## Sign-off

- Author (Codex): signed.
- Peer reviewer (Claude): pending.
