# Autonomy Runtime — WaggleDance v2.0

## Overview

The Autonomy Runtime is WaggleDance's solver-first, capability-driven engine.
It replaces the legacy LLM-first architecture with a 3-layer model:

| Layer | Priority | Source | Quality |
|-------|----------|--------|---------|
| **Layer 3** — Authoritative Core | 1st | Solvers, rules, policy | Gold |
| **Layer 2** — Specialist Models | 2nd | Locally trained models | Silver |
| **Layer 1** — LLM Servant | Last | Ollama (phi4-mini etc.) | Bronze |

LLM is never the authoritative decision-maker — it only serves as explainer,
labeler, and fallback when no solver or specialist model can answer.

## Architecture

```
AutonomyRuntime
├── GoalEngine          — goal lifecycle (propose → accept → plan → execute → verify)
├── Planner             — capability chain builder
├── SolverRouter        — solver-first routing (3-tier)
├── PolicyEngine        — deny-by-default evaluation
├── SafeActionBus       — safe execution with rollback
├── Verifier            — outcome validation
├── CaseTrajectoryBuilder — learning data capture
├── WorldModel          — unified situation picture
├── WorkingMemory       — short-term query context
├── CapabilityRegistry  — all available capabilities
└── ResourceKernel      — load management + admission control
```

## Two Call Paths

### 1. Query Path (`handle_query`)
Single query → solver-first → response.

Steps: Intent classification → World model enrichment → Capability selection →
Policy check → Execution → Verification → Case trajectory recording.

### 2. Mission Path (`execute_mission`)
Goal → plan → execute → verify.

Steps: Goal creation → Plan generation → Step-by-step execution →
Verification per step → World model update → Case trajectory recording.

## Quality Grading

| Grade | Criteria | Auto-promote? |
|-------|----------|---------------|
| **Gold** | Solver + verifier confirmed | Yes |
| **Silver** | Partial solver or specialist model | Needs 48h canary |
| **Bronze** | LLM-only response | Never auto-promote |
| **Quarantine** | Conflict or hallucination detected | Blocked |

## Configuration

```yaml
# configs/settings.yaml
runtime:
  primary: waggledance        # "hivemind" during transition
  compatibility_mode: false   # true = legacy primary
```

## Key Modules

| Module | Path |
|--------|------|
| Runtime | `waggledance/core/autonomy/runtime.py` |
| Goal Engine | `waggledance/core/goals/goal_engine.py` |
| Planner | `waggledance/core/planning/planner.py` |
| Solver Router | `waggledance/core/reasoning/solver_router.py` |
| Policy Engine | `waggledance/core/policy/policy_engine.py` |
| Safe Action Bus | `waggledance/core/actions/action_bus.py` |
| Verifier | `waggledance/core/reasoning/verifier.py` |
| World Model | `waggledance/core/world/world_model.py` |
| Resource Kernel | `waggledance/core/autonomy/resource_kernel.py` |
| Lifecycle | `waggledance/core/autonomy/lifecycle.py` |
| Compatibility | `waggledance/core/autonomy/compatibility.py` |
| Autonomy Service | `waggledance/application/services/autonomy_service.py` |

## Legacy Wiring

When `runtime.primary=waggledance`, the legacy codebase delegates to the
autonomy runtime:

- `core/chat_handler.py` → routes through `AutonomyRuntime.handle_query()`
- `core/night_mode_controller.py` → delegates to `NightLearningPipeline`
- `core/memory_engine.py` → writes go through `SafeActionBus` write proxy
- `core/elastic_scaler.py` → wrapped under `ResourceKernel`
- `core/training_collector.py` → feeds `CaseTrajectoryBuilder`
- `core/route_telemetry.py` → feeds specialist trainer

All bridges degrade gracefully — if autonomy modules are unavailable,
the legacy path handles the request.

## Cutover Validation

```bash
python -m waggledance.tools.validate_cutover
```

Checks all 30+ required modules, runtime mode, and core class instantiation.
Output: `FULL AUTONOMY MODE ENABLED — cutover valmis` when ready.
