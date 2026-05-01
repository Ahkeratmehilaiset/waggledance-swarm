# 2026-05-01 — Live-runtime hot-path bottlenecks (Phase 14 P1)

**Author:** Phase 14 P1, run from repo truth at `origin/main` `8f07d8f`.
**Composes onto:** `docs/journal/2026-04-30_runtime_harvest_and_scale_bottlenecks.md` (Phase 13 P1).
**Method:** file-level reading of `solver_router.py`, `solver_dispatcher.py`, `runtime_query_router.py`, `gap_intake.py`, plus inspection of the Phase 13 proof artifact.

## TL;DR

Phase 13 introduced `RuntimeQueryRouter` as a real runtime seam. The mass proof routed 68 structured queries through it and got 68 capability-aware hits with 0 provider calls. What Phase 13 did **not** do:

1. **No production code calls `RuntimeQueryRouter.route(...)`.** A test harness invokes it; the autonomy runtime does not. Until a real production handler invokes it under normal traffic, the autonomy slice is still proof-only.
2. **The hot path is SQLite-bound.** Every dispatch does `find_auto_promoted_solvers_by_features` (one SQLite SELECT/JOIN), `get_solver_artifact` (another SELECT), then `json.loads(artifact_json)`. That is fine at test scale (microseconds against `:memory:`-style DBs) but is one SQLite round-trip + one JSON parse *per query*. At hundreds of QPS against a real on-disk DB, that becomes the dominant cost.
3. **Miss path writes synchronously.** `RuntimeGapDetector.record(signal)` does an SQLite INSERT plus an `emit_growth_event` INSERT *per missed query*, blocking the caller. Under burst miss traffic this floods the runtime path.

Phase 14 must close all three.

## What is real today (post-Phase-13)

* **Real seam exists.** `waggledance/core/autonomy_growth/runtime_query_router.py:RuntimeQueryRouter.route(query)` — capability-aware dispatch then family-FIFO fallback then auto-emitted gap signal. Integration-tested through `tools/run_runtime_harvest_proof.py` and `tests/autonomy_growth/test_runtime_harvest_proof_smoke.py`.
* **Capability-aware dispatch.** `LowRiskSolverDispatcher.dispatch_by_features(family, features, inputs)` indexes on `solver_capability_features (family_kind, feature_name, feature_value)`. Verified at the unit-test level.
* **Inner-loop zero-provider invariant.** Locked by `tests/autonomy_growth/test_outer_inner_loop_truthful.py::test_mass_autogrowth_zero_provider_calls` and the Phase 13 smoke test.
* **Six-family allowlist.** `LOW_RISK_FAMILY_KINDS` constant + parity test against the policy doc.

## Where SQLite still touches the autonomy consult hot path

| Call | File:line | Cost per query |
|---|---|---|
| `LowRiskSolverDispatcher.dispatch_by_features` → `find_auto_promoted_solvers_by_features` | `waggledance/core/autonomy_growth/solver_dispatcher.py` (~ line 95) → `waggledance/core/storage/control_plane.py` `find_auto_promoted_solvers_by_features` (v4 helper) | one SQLite SELECT + JOIN, indexed but still a syscall + lock acquire |
| `dispatch_by_features` → `get_solver_artifact` | `waggledance/core/storage/control_plane.py` `get_solver_artifact` | one SQLite SELECT |
| `dispatch_by_features` → `json.loads(artifact_record.artifact_json)` | `waggledance/core/autonomy_growth/solver_dispatcher.py` (~ line 130) | one Python JSON parse (allocations + dict construction) per warm hit |
| `dispatch_by_features` → solver-name lookup `SELECT name FROM solvers WHERE id = ?` | `solver_dispatcher.py` (~ line 152) | one more SQLite SELECT |
| `RuntimeQueryRouter.route` miss path → `RuntimeGapDetector.record` | `gap_intake.py` (~ line 58) | one SQLite INSERT + one `emit_growth_event` INSERT + Python lock contention |

That is **four SQLite round-trips + one JSON parse per warm hit**, and **two SQLite INSERTs per miss**. Phase 14 P3 must collapse both paths.

## Where production runtime currently does or does not see the seam

* `waggledance/core/reasoning/solver_router.py:SolverRouter.route(intent, query, context)` — production solver-first reasoning router. Used by `waggledance/core/autonomy/runtime.py`. Tested by `tests/autonomy/test_solver_router.py`. **Does not currently call `RuntimeQueryRouter`.**
* `waggledance/core/autonomy/runtime.py` — autonomy runtime; consumes `SolverRouter`. Wiring inside `SolverRouter` naturally surfaces here without a separate change.
* `waggledance/core/api_distillation/api_consultant.py` — outer-loop teacher / API-call surface. Not the inner-loop entrypoint.
* `waggledance/core/providers/provider_plane.py` — provider plane dispatch; outer-loop.

`SolverRouter.route` is the narrowest truthful seam in the production reasoning pipeline. Phase 14 P2 wires the autonomy consult into it as a **backwards-compatible context-hint extension** (`context["low_risk_autonomy_query"]`) so existing callers see no behaviour change.

## What Phase 13 still left missing (closed by Phase 14)

| Order | Blocker | Closed by |
|---|---|---|
| 1 | **Router not wired into production code.** | **P2** — `SolverRouter.route` calls into `RuntimeQueryRouter` when the caller passes a `low_risk_autonomy_query` hint. |
| 2 | **SQLite per warm hit.** | **P3** — `WarmCapabilityIndex` keyed on `(family_kind, frozenset(features))` → `solver_id`; populated lazily; invalidated on promotion / deactivation. |
| 3 | **JSON parse per warm hit.** | **P3** — `ParsedArtifactCache` keyed on `solver_id` → parsed dict; invalidated on artifact change. |
| 4 | **Synchronous SQLite INSERT on every miss.** | **P3** — `BufferedSignalSink` with bounded queue (1000 entries / 500 ms), explicit flush + atexit. Documented hard-kill loss bound. |
| 5 | **Proof bypasses production handler.** | **P5** — `tools/run_live_runtime_hotpath_proof.py` exercises queries through `SolverRouter.route(...)` with the new context hint. |
| 6 | **Latency unmeasured.** | **P5** — proof reports cold/warm p50/p95/p99 and compares against the P3 acceptance thresholds. |

## What still depends on Claude Code

Unchanged from Phase 13. Restating:

* **Inner loop, common path:** zero. Verified by test (delta-checked over the proof window in P6).
* **Outer loop:** spec invention for unknown patterns and family extension still depend on `claude_code_builder_lane` (LLM solver generator priority list, position 1).

## What still depends on a human gate

Unchanged. Phase 9 14-stage runtime stages, Stage-2 atomic flip, the four excluded families, all actuator/policy writes, capsule deployment, family extension. RULE 19 honoured this session — no widening of the allowlist.

## What blocks 100 / 1000 / 10000 deterministic solvers (post-Phase-14 if landed green)

| Order | Blocker | Status after Phase 14 |
|---|---|---|
| 1 | No production handler invokes the seam | **closed** by P2 |
| 2 | SQLite + JSON-parse cost per query | **closed** by P3 warm cache |
| 3 | SQLite write per miss | **closed** by P3 buffered sink |
| 4 | Family allowlist is small (six) | unchanged by design (RULE 19) |
| 5 | No real Anthropic / OpenAI HTTP adapters | unchanged; outer-loop concern |
| 6 | No Stage-2 runtime read-path migration | unchanged; `STAGE2_CUTOVER_RFC.md` |
| 7 | Single-process scope | unchanged by design (RULE 20: no sharding/federation this session) |
| 8 | No capsule-aware overlay | unchanged |

After Phase 14, the path to **hundreds** of low-risk solvers becomes operationally tractable on real production traffic for the in-scope query class. The path to **thousands** still needs broader harvest scope (more handler types calling the seam) and richer feature dimensions per family — both follow-up work.

## Minimum Phase 14 slice that materially improves real autonomy without widening risk

1. Wire `SolverRouter.route` to `RuntimeQueryRouter` via a backwards-compatible context hint (P2).
2. Add `WarmCapabilityIndex` + `ParsedArtifactCache` so the warm path skips SQLite + JSON (P3).
3. Add `BufferedSignalSink` with bounded queue + flush contract (P3).
4. Expand the canonical seed library to 96+ if safe (P4).
5. Author a proof script that runs the corpus through `SolverRouter.route` with the new hint (P5).
6. Lock the zero-provider-delta invariant via test (P6).
7. Extend the existing `autonomy_runtime_harvest_kpis` Reality View panel with hot-path counters (P7) — no new panel.

This stays inside the six-family allowlist, does not introduce sharding or federation, does not touch Stage-2 cutover, does not collect HUMAN_APPROVAL, and does not overclaim adapters.

## Scorecard — pre-Phase-14 (current truth)

```
structural_readiness_0_to_100         : 84
operational_autonomy_0_to_100         : 60
live_runtime_integration_0_to_100     : 25   # router exists; production handler does not call it
hotpath_latency_readiness_0_to_100    : 20   # SQLite + JSON parse per warm hit; sync write per miss
capability_lookup_scale_readiness_0_to_100 : 55   # indexed but not memory-resident
teacher_lane_dependence_0_to_100      : 20
human_gate_dependence_0_to_100        : 55
path_to_100_solvers_status            : "operationally tractable; bottleneck is hot-path latency under real call cadence and not having a real production handler call the seam"
path_to_1000_solvers_status           : "blocked on hot-path cost (closed by P3) + broader harvest scope (next phase)"
path_to_10000_solvers_status          : "blocked on broader harvest scope, richer feature dimensions, and outer-loop adapter diversity"
```

## Scorecard — targeted post-Phase-14 (only valid if P2/P3/P5 land green)

```
structural_readiness_0_to_100         : 88   # +4 from production wiring + warm cache layer
operational_autonomy_0_to_100         : 72   # +12 because production handler now feeds the loop
live_runtime_integration_0_to_100     : 65   # +40 because SolverRouter.route is now the wiring point
hotpath_latency_readiness_0_to_100    : 60   # +40 because warm path is memory-resident; miss path buffered
capability_lookup_scale_readiness_0_to_100 : 75   # +20 from in-memory index over the v4 features
teacher_lane_dependence_0_to_100      : 18   # essentially flat; outer-loop unchanged
human_gate_dependence_0_to_100        : 50   # -5 because the trigger now happens on real handler traffic
path_to_100_solvers_status            : "operationally proven on the chosen entrypoint; reproducible by the live proof"
path_to_1000_solvers_status           : "now bounded by handler scope and per-family feature richness; both addressable in follow-up phases"
path_to_10000_solvers_status          : "still needs broader harvest + richer features + outer-loop adapter diversity"
```

If P2/P3/P5 do not land green, these numbers stay at the pre-Phase-14 row and Phase 14 emits a stop-and-handoff per RULE 13.

## Cross-references

* `docs/architecture/LOW_RISK_AUTOGROWTH_POLICY.md` — Phase 11 policy envelope; unchanged.
* `docs/runs/phase11_autogrowth_2026_04_29/autonomy_proof.md` — Phase 11 manual proof.
* `docs/runs/phase12_self_starting_autogrowth_2026_04_30/autonomy_proof.md` — Phase 12 self-starting synthetic harness.
* `docs/runs/phase13_runtime_harvest_2026_04_30/runtime_harvest_proof.md` — Phase 13 router proof (still synthetic-script-driven).
* `tests/autonomy/test_solver_router.py` — production reasoning router tests (the new wiring extends this).
* `tests/autonomy_growth/test_outer_inner_loop_truthful.py` — locks the inner-loop zero-provider invariant.
