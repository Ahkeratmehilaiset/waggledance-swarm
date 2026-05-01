# Runtime Entrypoint Truth Map (Phase 15 P1)

**Status:** authored 2026-05-01 from repo state at `origin/main` `2733f87`. This is the truthful inventory of every place in `waggledance/` that calls into the autonomy reasoning router or its caller chain. Phase 15 selects exactly one production caller above `SolverRouter.route(...)` to wire the deterministic hint extractor into.

**Hard gate (RULE):** `SolverRouter.route(...)` itself is **not eligible** as the selected caller in Phase 15. `RuntimeQueryRouter.route(...)` is **not eligible**. The selected caller must be above `SolverRouter` in the production / service / runtime stack and must already receive normal structured runtime / service input.

## Caller chain diagram

```
external caller / service test
        │
        ▼
AutonomyService.handle_query(query, context, priority)         ← service-layer (waggledance/application/services/autonomy_service.py)
        │ admission control + metrics
        ▼
CompatibilityLayer.handle_query(query, context)                ← compatibility-mode shim (waggledance/core/autonomy/compatibility.py)
        │ legacy primary OR autonomy primary
        ▼
AutonomyRuntime.handle_query(query, context)                   ← production query handler (waggledance/core/autonomy/runtime.py:335)
        │ admission, intent classification, capsule match, world snapshot
        │ context["profile"], context["capsule_decision"], etc.
        ▼
SolverRouter.route(intent, query, context)                     ← reasoning router (waggledance/core/reasoning/solver_router.py:90)
        │ capability selection (Phase 9 / Phase 14 autonomy_consult here)
        ▼
LowRiskSolverDispatcher / RuntimeQueryRouter / autonomy growth lane (Phase 11–14)
```

## Candidate inventory + scoring

| File:function | Eligible above SolverRouter? | Real runtime? | Sees structured low-risk traffic? | Safely testable in-process? | Decision |
|---|---|---|---|---|---|
| `waggledance/core/reasoning/solver_router.py::SolverRouter.route` | **NO — gate forbids it** | yes | n/a (the router is the gate) | n/a | rejected: this IS the seam Phase 14 already wired the consult into |
| `waggledance/core/autonomy_growth/runtime_query_router.py::RuntimeQueryRouter.route` | **NO — gate forbids it** | yes | n/a (autonomy lane router) | n/a | rejected: this is the autonomy lane the consult adapter calls into |
| `waggledance/core/autonomy/runtime.py::AutonomyRuntime.handle_query` | **YES** | yes — production query handler invoked by AutonomyService and tools | yes — accepts open-ended `context: Dict[str, Any]` that callers naturally use to pass typed runtime data (profile, capsule_decision, sensor readings, structured requests, etc.) | yes — constructible in-process with no MAGMA / no DB; existing tests in `tests/autonomy/` use the same constructor | **SELECTED** |
| `waggledance/core/autonomy/compatibility.py::CompatibilityLayer.handle_query` | yes | wrapper around `AutonomyRuntime.handle_query` (or legacy) | inherits from `AutonomyRuntime` | yes | rejected: thin wrapper. Wiring inside `AutonomyRuntime.handle_query` naturally surfaces here without a separate change. |
| `waggledance/application/services/autonomy_service.py::AutonomyService.handle_query` | yes | service-layer orchestration (admission, metrics) above `CompatibilityLayer.handle_query` | inherits from below | yes — but heavyweight; constructs lifecycle/admission/metrics components | rejected: too far above. The `(query, context)` shape is identical at this layer; the hint extractor is naturally in `AutonomyRuntime.handle_query` so every caller from `AutonomyService` downward inherits the wiring. |
| `waggledance/core/autonomy/compatibility.py::CompatibilityLayer.handle_query` legacy path | yes | legacy `HiveMind.handle_query` path; out of scope | varies | yes | rejected: legacy path is being deprecated; new wiring should target the autonomy primary path |
| `waggledance/core/autonomy_growth/autonomy_consult_adapter.py` callable | n/a | this is the bridge that `SolverRouter.route` calls into when the hint is set | n/a | n/a | rejected: this is what Phase 14 already wired |

## Rejected candidates summary

* **`SolverRouter.route` and `RuntimeQueryRouter.route`** — explicitly forbidden by the operator's "CALLER ELIGIBILITY HARD GATE". They are the seam Phase 14 wired into; Phase 15 must choose something *above* them.
* **`CompatibilityLayer.handle_query`** — thin pass-through above `AutonomyRuntime.handle_query`. Wiring inside `AutonomyRuntime.handle_query` automatically surfaces through `CompatibilityLayer.handle_query` (when `compatibility_mode=False`, which is the default per Phase 9 / `validate_cutover.py`). No additional work needed.
* **`AutonomyService.handle_query`** — too far above. Identical `(query, context)` signature; the hint extractor is naturally one level lower in `AutonomyRuntime.handle_query`. Inheriting via the natural call chain is cleaner and avoids putting autonomy-growth logic into the service-layer admission/metrics module.
* **Legacy `HiveMind.handle_query`** — explicitly out of scope; the autonomy primary path is the future.

## Selected caller — `AutonomyRuntime.handle_query`

* **File:function:** `waggledance/core/autonomy/runtime.py:335` — `AutonomyRuntime.handle_query(self, query: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]`.
* **Already on production runtime?** Yes. Called by `CompatibilityLayer.handle_query` (when autonomy primary), which is called by `AutonomyService.handle_query`, which is the service-layer entrypoint in `application/services/`.
* **Normal input shape — already part of the contract:**
  * `query: str` — caller's free text.
  * `context: Dict[str, Any]` — open-ended dict. Existing production code already populates `context["profile"]`, `context["capsule_decision"]`, `context["capsule_layer"]`, `context["capsule_confidence"]` inside `handle_query`. Callers pass arbitrary additional keys for downstream use (sensor readings, structured requests, identifying tags, etc.). The Phase 15 hint extractor reads `context.get("structured_request")` — a natural use of this open dict, not a renamed `low_risk_autonomy_query`.
* **Built-in solver precedence preserved:** Phase 14's `SolverRouter.route` already enforces this. The hint extractor only adds `context["low_risk_autonomy_query"]`; it does not bypass capability selection. The autonomy consult fires only when `selection.fallback_used` (Phase 14 contract).
* **Existing tests:** `tests/autonomy/test_solver_router.py` (production reasoning router) plus `AutonomyRuntime` tests under `tests/autonomy/`. The Phase 15 wiring is exercised by a new test set that constructs a minimal `AutonomyRuntime` and calls `handle_query(...)` with `context["structured_request"]` populated.
* **Provider fallback risk:** zero. The hint extractor is deterministic Python; no LLM, no provider call. The autonomy consult lane is local-first per Phase 14 truth (locked by `test_outer_inner_loop_truthful.py`).

## Why a "structured_request" context field is not a renamed hint

The operator's rule: "Do not add a new field that is merely `low_risk_autonomy_query` under another name."

`context["structured_request"]` differs from `low_risk_autonomy_query` in three ways:

1. **It pre-existed as a use-pattern.** `context: Dict[str, Any]` is open-ended by API design; production callers already pass typed structured data inside `context` for any downstream component to consume (e.g., capsule decision matching, world model enrichment). The `structured_request` slot is one such use.
2. **It is family-shaped, not autonomy-shaped.** Each subkey (`unit_conversion`, `lookup`, `threshold_check`, `bucket_check`, `linear_eval`, `interpolation`) is named after the natural domain operation, not after the autonomy lane. The autonomy hint is *derived* from these by the extractor; the field itself is the typed payload a caller would pass for any reason.
3. **The extractor refuses to fabricate.** When `structured_request` is absent or its content is ambiguous / mis-shaped, the extractor returns `skipped` / `rejected_*`. There is no path where the extractor produces a hint without an explicit, structured caller payload.

The proof's regression test (`test_automatic_runtime_hint_proof_smoke.py::test_proof_input_carries_no_manual_low_risk_hint`) asserts that the four forbidden literal keys (`low_risk_autonomy_query`, `family_kind`, `features`, `spec_seed`) never appear in the proof corpus input. Only the `structured_request` payload does.

## Cross-references

* `waggledance/core/reasoning/solver_router.py` — Phase 14 autonomy consult seam.
* `waggledance/core/autonomy_growth/autonomy_consult_adapter.py` — bridge to `RuntimeQueryRouter`.
* `waggledance/core/autonomy_growth/runtime_query_router.py` + `hot_path_cache.py` — Phase 13 + 14 lane.
* `tests/autonomy/test_solver_router.py` — production reasoning router tests.
* Phase 14 proof: `tools/run_live_runtime_hotpath_proof.py`.
* Phase 15 proof (this PR): `tools/run_automatic_runtime_hint_proof.py`.
