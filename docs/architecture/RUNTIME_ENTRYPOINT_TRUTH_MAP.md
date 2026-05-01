# Runtime Entrypoint Truth Map (Phase 15 P1 + Phase 16A P1)

**Status:** initially authored 2026-05-01 for Phase 15 from repo state at `origin/main` `2733f87`. **Phase 16A P1 update appended** at the end of this document, dated 2026-05-01, from repo state at `origin/main` `2b9978d` (post-Phase-15 merge).

This is the truthful inventory of every place in `waggledance/` that calls into the autonomy reasoning router or its caller chain. Phase 15 selected exactly one production caller above `SolverRouter.route(...)` to wire the deterministic hint extractor into. Phase 16A selects exactly one production caller **above** `AutonomyRuntime.handle_query(...)` to derive `context["structured_request"]` from natural upstream input.

**Hard gate (RULE):** `SolverRouter.route(...)` itself is **not eligible**. `RuntimeQueryRouter.route(...)` is **not eligible**. The Phase 15 selected caller must be above `SolverRouter`. The Phase 16A selected caller must be above `AutonomyRuntime.handle_query` (i.e. above the Phase 15 wiring point).

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

---

## Phase 16A P1 update (2026-05-01)

Phase 16A selects the **upstream caller above `AutonomyRuntime.handle_query`** that automatically builds `context["structured_request"]` from its natural input shape, *before* `AutonomyRuntime.handle_query` runs the Phase 15 runtime-hint extractor.

### Phase 16A caller chain (post-Phase-15)

```
external HTTP / CLI / service caller
        │
        ▼
AutonomyService.handle_query(query, context, priority)         ← PHASE 16A SELECTED CALLER
        │ admission control + priority + KPIs
        │ NEW: derive context["structured_request"] from natural flat fields in context
        ▼
CompatibilityLayer.handle_query(query, context)                ← thin shim (legacy / autonomy primary)
        │
        ▼
AutonomyRuntime.handle_query(query, context)                   ← Phase 15 selected caller
        │ NEW (Phase 15): derive context["low_risk_autonomy_query"] from context["structured_request"]
        ▼
SolverRouter.route(intent, query, context)                     ← Phase 14 autonomy consult seam
        │
        ▼
LowRiskSolverDispatcher / RuntimeQueryRouter / autonomy growth lane
```

### Phase 16A candidate inventory

| File:function | Above `handle_query`? | Real runtime? | Distinct upstream input shape? | Naturally testable? | Decision |
|---|---|---|---|---|---|
| `waggledance/application/services/autonomy_service.py::AutonomyService.handle_query` | yes | yes — service-layer entrypoint, registered in FastAPI lifespan and `get_autonomy_service` DI | yes — `(query: str, context: Dict[str, Any], priority: int)` adds the service-layer `priority` parameter and admission/metrics surface; production callers populate `context` with flat domain fields | yes — `tests/autonomy/test_wiring_integration.py` already constructs `AutonomyService()` directly | **SELECTED** |
| `waggledance/core/autonomy/compatibility.py::CompatibilityLayer.handle_query` | yes | yes — wraps `AutonomyRuntime.handle_query` (or legacy) | no — identical `(query, context)` signature; pure pass-through with `source` tag | yes | rejected: thin pass-through with no distinct upstream input. Wiring at `AutonomyService.handle_query` automatically surfaces through this shim. |
| `waggledance/application/services/chat_service.py::ChatService.handle` | yes (different lane) | yes — chat HTTP endpoint | yes (`ChatRequest` DTO) | yes | rejected: **does not flow through `AutonomyRuntime.handle_query`** — uses `Orchestrator` directly. Out of scope for Phase 16A. |
| `waggledance/application/services/autonomy_service.py::AutonomyService.execute_mission` | yes | yes | yes — mission-shaped (`goal_type`, `description`, `priority`, `context`) | yes | rejected: mission-shaped input does not map to low-risk solver families. Out of scope for Phase 16A. |
| `waggledance/adapters/http/routes/autonomy.py` | n/a | yes — FastAPI routes | n/a | n/a | rejected: no `/autonomy/query` route currently exists; the existing autonomy HTTP routes are status/KPI/learning/safety, not query. Adding a route is broader than Phase 16A scope. |
| `tools/run_automatic_runtime_hint_proof.py::_run_query_through_runtime` | n/a | proof-only harness | n/a | n/a | rejected: proof-only — explicitly forbidden by Phase 16A "must be a real upstream caller" rule. |
| `waggledance/core/autonomy/runtime.py::AutonomyRuntime.handle_query` | NO — Phase 16A gate forbids | n/a | n/a | n/a | rejected: explicitly forbidden by Phase 16A gate (Phase 15 already wired the runtime-hint extractor here). |
| `waggledance/core/reasoning/solver_router.py::SolverRouter.route` | NO — Phase 16A gate forbids | n/a | n/a | n/a | rejected: explicitly forbidden by Phase 16A gate. |
| `waggledance/core/autonomy_growth/runtime_query_router.py::RuntimeQueryRouter.route` | NO — Phase 16A gate forbids | n/a | n/a | n/a | rejected: explicitly forbidden by Phase 16A gate. |

### Phase 16A selected caller — `AutonomyService.handle_query`

* **File:function:** `waggledance/application/services/autonomy_service.py:239` — `AutonomyService.handle_query(self, query: str, context: Optional[Dict[str, Any]] = None, priority: int = 50) -> Dict[str, Any]`.
* **Distinct upstream input shape:** the service-layer adds the `priority: int` parameter (used for admission control / scheduling) and a service-elapsed metric. Importantly, the *natural* `context` shape at the service layer is a flat domain dict that production / API callers would populate with fields like `operation`, `from_unit`, `to_unit`, `value`, `table`, `key`, `subject`, `x`, `threshold`, `comparator`, `inputs`, `x_var`, `y_var`. The Phase 16A upstream extractor *lifts* these flat fields into the nested `context["structured_request"]` shape that Phase 15's runtime-hint extractor reads.
* **Why this is not "structured_request under another name":**
  1. Caller-supplied flat fields are domain-named, not autonomy-named (`from_unit`, `value`, `subject`, `x`) — no field is called `structured_request`.
  2. The lifting `flat → nested` is a real transformation; a flat caller payload like `{"operation": "unit_conversion", "from_unit": "C", "to_unit": "F", "value": 25}` becomes `context["structured_request"] = {"unit_conversion": {"from_unit": "C", "to_unit": "F", "value": 25}}`.
  3. The upstream extractor refuses to fabricate. When `context["operation"]` is absent / unrecognised / missing required fields, it returns `skipped` / `rejected_*` and writes nothing.
  4. Phase 15's runtime-hint extractor still runs unchanged at the `AutonomyRuntime.handle_query` layer; the upstream extractor is purely additive.
* **Built-in solver precedence preserved:** Phase 14's `SolverRouter.route` still enforces fallback-only autonomy consult; the upstream extractor only writes `context["structured_request"]` and never bypasses capability selection.
* **Provider fallback risk:** zero. The upstream extractor is deterministic Python — no LLM, no embedding, no provider call.
* **Existing tests:** `tests/autonomy/test_wiring_integration.py` already constructs `AutonomyService()` directly. Phase 16A adds new tests under `tests/autonomy_growth/test_upstream_structured_request_extractor.py` and `tests/autonomy_growth/test_autonomy_service_upstream_wiring.py`.

### Why CompatibilityLayer was rejected for Phase 16A

`CompatibilityLayer.handle_query(query, context)` has the same signature as `AutonomyRuntime.handle_query` and acts as a thin pass-through with a `source` tag. Wiring the upstream extractor here would be functionally identical to wiring it inside `AutonomyRuntime.handle_query` (which is the Phase 15 wiring point and is forbidden by Phase 16A). Wiring it at `AutonomyService.handle_query` is materially distinct because:

* the service-layer `priority`/admission surface is meaningful production state,
* `AutonomyService.handle_query` is the FastAPI dependency-injected entrypoint (`get_autonomy_service` DI), so any future HTTP route would naturally flow through here,
* admission rejection / deferral is recorded *before* the upstream extractor runs, preserving service-layer correctness.

### Phase 16A cross-references

* Phase 16A upstream extractor: `waggledance/core/autonomy_growth/upstream_structured_request_extractor.py`.
* Phase 16A wiring point: `waggledance/application/services/autonomy_service.py::AutonomyService.handle_query`.
* Phase 16A proof: `tools/run_upstream_structured_request_proof.py`.
* Phase 16A proof artifacts: `docs/runs/phase16_upstream_structured_request_2026_05_01/upstream_structured_request_proof.{md,json}`.
