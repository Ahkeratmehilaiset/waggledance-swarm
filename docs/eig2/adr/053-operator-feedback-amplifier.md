# ADR-053 — Operator-feedback amplifier (scheduler queue priority)

* Status: **scheduler-preflight landing** (contract + ADR pinned; pure planner implemented; scheduler preflight artifact implemented; bridge writer, scheduler enqueue/execution, and UI deferred)
* Date: 2026-05-12
* Related: ADR-048 (portfolio promotion), ADR-049 (sleep consolidation)

## Context

Operator feedback ("this query is broken; need a new solver") today must work through hand-routing or full gap-mining cycle. Hours/days to get the relevant candidate into the candidate queue. The 50-leaps menu (L30) calls for **operator-feedback amplifier**: explicit `ops_feedback` event gives the relevant solver candidate scheduler queue priority in 1 event, without skipping promotion gates.

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

This landing implements the pure planner plus a scheduler preflight artifact.
The planner validates `ops_feedback` and returns a sanitized
`feedback_action_taken` action plan. The preflight derives `operator_id` from a
verified bridge event envelope present in the durable bridge log, rate-limits
against durable bridge-log feedback events, and renders a scheduler candidate
artifact. It does not write to `events.jsonl`, enqueue growth intents, run the
scheduler, mutate solver state, or grant runtime authority until the separate
bridge writer and scheduler execution integrations land.

Future `autogrowth_scheduler` execution can consume the preflight artifact, and
on `ops_feedback`:

1. If `feedback_kind="needs_solver"`: spawns a deep-bin gap_signal (per ADR-031) for that query_class_hash, with `priority="high"` flag -> scheduler queue priority only.
2. If `feedback_kind="broken_route"`: requires `route_context_hash` and triggers an L40 negative-tunnel mining plan for that route context; missing route context fails closed with `route_context_required`.
3. If `feedback_kind="wrong_output"`: same as needs_solver but also adds query to ADR-034 anti-cargo-cult probe set as a confirmed adversarial input.

Priority="high" feedback gets `fast_track_canary_minutes=15` (vs normal 24h
cycle), but this is only queue priority. It never skips canary, adversarial, or
promotion gates.

## Invariants (OFA-001..OFA-010)

1. **Event schema**: required fields event_type, feedback_id, feedback_kind, query_class_hash, operator_id, priority, submitted_at_utc; `broken_route` additionally requires `route_context_hash`.
2. **Feedback kind enum**: `{needs_solver, broken_route, wrong_output}`.
3. **Priority enum**: `{high, normal}`.
4. **High priority fast-track 15 min**: pinned in contract as queue priority only.
5. **Operator identity required**: anonymous feedback rejected; scheduler preflight derives `operator_id` from the verified bridge event envelope, not a free-string payload.
6. **Auditable plan**: every accepted ops_feedback event produces a `feedback_action_taken` action plan linking `feedback_id` to `action_id`; this planner keeps `bridge_event_written=false` until the separate bridge writer integration persists the echo audit event.
7. **Bounded amplification**: max `fast_track_per_hour=10` and `fast_track_global_per_hour=30` prevent per-operator and global feedback storms; excess queued normally.
8. **Durable rate-limit source**: scheduler preflight counts persisted bridge-log feedback events, not in-memory caller state.
9. **Priority only**: `gate_skip_allowed=false`; canary, adversarial, and promotion gates are never skipped.

Contract: `docs/eig2/contracts/operator_feedback_amplifier.json`. Pure planner
and scheduler preflight:
`waggledance/core/autonomy_growth/operator_feedback_amplifier.py`. Tests:
`tests/contracts/test_operator_feedback_amplifier.py` and
`tests/autonomy_growth/test_operator_feedback_amplifier.py`.
