# ADR-053 — Operator-feedback amplifier (fast-track to canary)

* Status: **planner landing** (contract + ADR pinned; pure planner implemented; bridge writer, scheduler hookup, and UI deferred)
* Date: 2026-05-12
* Related: ADR-048 (portfolio promotion), ADR-049 (sleep consolidation)

## Context

Operator feedback ("this query is broken; need a new solver") today must work through hand-routing or full gap-mining cycle. Hours/days to get the relevant candidate into canary. The 50-leaps menu (L30) calls for **operator-feedback amplifier**: explicit `ops_feedback` event fast-tracks the relevant solver candidate to canary in 1 event.

## Decision

A new bridge event `ops_feedback`:

```json
{
  "event_type": "ops_feedback",
  "feedback_id": "<uuid>",
  "feedback_kind": "needs_solver | broken_route | wrong_output",
  "query_class_hash": "<sha256>",
  "route_context_hash": "<sha256; required only for broken_route>",
  "operator_id": "<id>",
  "priority": "high | normal",
  "submitted_at_utc": "<ISO8601>"
}
```

`autogrowth_scheduler` polls events, and on `ops_feedback`:

1. If `feedback_kind="needs_solver"`: spawns a deep-bin gap_signal (per ADR-031) for that query_class_hash, with `priority="high"` flag → fast-tracked to canary lane.
2. If `feedback_kind="broken_route"`: requires `route_context_hash` and triggers an L40 negative-tunnel mining plan for that route context; missing route context fails closed with `route_context_required`.
3. If `feedback_kind="wrong_output"`: same as needs_solver but also adds query to ADR-034 anti-cargo-cult probe set as a confirmed adversarial input.

Priority="high" feedback gets `fast_track_canary_minutes=15` (vs normal 24h cycle).

## Invariants (OFA-001..OFA-007)

1. **Event schema**: required fields event_type, feedback_id, feedback_kind, query_class_hash, operator_id, priority, submitted_at_utc; `broken_route` additionally requires `route_context_hash`.
2. **Feedback kind enum**: `{needs_solver, broken_route, wrong_output}`.
3. **Priority enum**: `{high, normal}`.
4. **High priority fast-track 15 min**: pinned in contract.
5. **Operator identity required**: anonymous feedback rejected.
6. **Auditable**: every ops_feedback event triggers a `feedback_action_taken` event in response (echo audit trail).
7. **Bounded amplification**: max `fast_track_per_hour=10` to prevent feedback storms; excess queued normally.

Contract: `docs/eig2/contracts/operator_feedback_amplifier.json`. Pure planner:
`waggledance/core/autonomy_growth/operator_feedback_amplifier.py`. Tests:
`tests/contracts/test_operator_feedback_amplifier.py` and
`tests/autonomy_growth/test_operator_feedback_amplifier.py`.
