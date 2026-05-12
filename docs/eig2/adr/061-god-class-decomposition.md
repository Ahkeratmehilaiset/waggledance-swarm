# ADR-061 — God-class decomposition strategy for container + autonomy runtime

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: PR #284 (L51 fan-in contracts), ADR-036 (L36 Container @cached_property pin)

## Context

Import-graph scan (`.tmp-claude-1000-obs/import-graph.json`) identified two god-classes in the codebase:

* `waggledance/bootstrap/container.py`: 933 lines, **47 fan-out** to internal modules, **8 fan-out** to external libs. Transitive closure: 167 internal + 74 external. The single largest coupling point.
* `waggledance/core/autonomy/runtime.py`: ~56 KB, **28 fan-out** internal, 11-positional-arg `__init__`. Transitive closure: 79 internal + 29 external.

L52 from the 50-leaps menu calls for decomposing these into focused sub-modules. The benefit is **per-PR blast-radius reduction**, not cold-import savings — transitive deps redistribute, they do not disappear (per Claude's transitive-depth measurement at `.tmp-claude-1000-obs/import-depth.json`).

This is a **medium-risk multi-PR** refactor. This ADR pins the **decomposition strategy** so the actual splits land incrementally without losing the invariants.

## Decision

### container.py — Five-way split

| Sub-module | Responsibility | Approx. cached_property keys (per PR #294) |
|---|---|---|
| `container/storage.py` | DB lifecycle, control_plane_db, runtime_gap_detector | control_plane_db, faiss_registry, storage_health |
| `container/llm.py` | LLM chain, bridge_llm_client, gemma_router | llm, gemma_router, gemma_verifier_advisor |
| `container/memory.py` | Vector store, memory repo, shared memory, hot cache | vector_store, memory_repository, shared_memory, hot_cache, hybrid_retrieval, hybrid_observer, hybrid_backfill |
| `container/agents.py` | Agent loading, lifecycle, autonomy services | _load_agents, autogrowth_scheduler, autogrowth_background_ticker, scheduler, orchestrator, autonomy_service |
| `container/services.py` | Application services + resource management | memory_service, chat_service, learning_service, readiness_service, night_pipeline, elastic_scaler, adaptive_throttle, resource_guard, priority_lock, parallel_dispatcher, hex_*, solver_candidate_lab, synthetic_accelerator, feed_ingest_sink, data_feed_scheduler |

The `Container` class composes the sub-modules via mixin or composition. PR #294 (L36) `@cached_property` pin still applies — each sub-module preserves the decorator pattern. The L51 contract tests (#284) continue to gate field shape.

### autonomy/runtime.py — Three-way split

| Sub-module | Responsibility |
|---|---|
| `autonomy/runtime/core.py` | The orchestrator entry points: `handle_query()`, `execute_mission()` |
| `autonomy/runtime/wiring.py` | The 11-arg `__init__` and component composition |
| `autonomy/runtime/policy.py` | Action bus + safe-action policy enforcement |

Same composition pattern.

## Consequences

### Per-PR blast radius

* Touching container.storage.py no longer requires re-running the entire Container test surface — only storage-dependent tests.
* PR reviewers see WHICH subsystem changed in the file path alone.

### Cold import

* No change: transitive deps redistribute, not shrink. PR #281's lazy-init substrate is the cold-import lever; god-class decomposition is purely organizational.

### Operational

* Each split PR is bounded: one sub-module per PR. 5 + 3 = 8 PRs total.
* Each PR keeps the Container/Runtime public surface unchanged. External callers `container.llm()`, `container.orchestrator()`, etc., continue to work.

## Invariants

Pinned in `docs/eig2/contracts/god_class_decomposition.json` and verified by `tests/contracts/test_god_class_decomposition.py`.

1. **Public surface preserved.** External callers (tests, services) MUST be able to do `from waggledance.bootstrap.container import Container; c.llm`, `c.control_plane_db`, etc. No symbol rename.
2. **@cached_property pin honored.** Per ADR-036 (PR #294) — each sub-module preserves the decorator on the 39 service accessors.
3. **L51 contract test gates field shape.** PR #284's `test_fan_in_public_surface_contracts` continues to pass on every split PR.
4. **No transitive-depth claim.** ADR explicitly forecloses "this saves cold-boot time" — the claim is blast-radius reduction only.
5. **Incremental landing.** 5 sub-module PRs for container + 3 for autonomy/runtime = 8 PRs total. Each independently mergeable.
6. **Reviewable in <30 minutes each.** Each PR moves ≤ 200 lines from monolith to sub-module + composition. No big-bang refactor.
7. **Composition over inheritance.** Sub-modules attach via Container instance properties (composition), not multiple inheritance.

## Out of scope (this ADR)

* The 8 implementation PRs themselves — each a separate PR.
* Renaming public symbols — explicitly forbidden by INV-1.
* Further decomposition into 6+ container sub-modules — operator-driven decision based on how the 5-way split holds up.

## References

* `.tmp-claude-1000-obs/import-graph.json` (fan-in/fan-out evidence)
* `.tmp-claude-1000-obs/import-depth.json` (transitive depth measurements)
* PR #284 (L51 fan-in contracts; gates this refactor)
* PR #294 (L36 @cached_property pin; gates this refactor)
* 50-leaps menu: L52 (this), L51, L36
