# Honeycomb Solver Scaling — Architecture

- **Status:** scaffolding / experimental
- **Owners:** WaggleDance core
- **Scope:** design document for Phase 8. No production runtime changes implied by this document alone; each change lands through its own reviewed commit.

## 0. Guiding principles (kept, not reopened)

1. **Solver-first, LLM-last.** The router selects deterministic solvers by default; LLM fallback is the last resort and is measured.
2. **Verifier / world-model validation.** Every candidate output is checked against the world model and domain invariants before it influences a response.
3. **GOLD / SILVER / BRONZE / QUARANTINE quality gates.** Promotion is earned, not declared.
4. **MAGMA audit trail.** Every solver call, fallback, and proposal has a replayable trace.
5. **Canary deployment.** New capability is shadowed, then rolled in behind a flag, then promoted. Never hot-merged.
6. **Hexagonal / ports-and-adapters.** New capability is additive — ports and adapters, not forks of existing pipelines.
7. **Local-first single-cell reliability.** One machine must keep working even when the fleet is offline.

"Consciousness" is a metaphor in user-facing copy only. In code and docs the measurable proxies are: **coverage**, **LLM fallback rate**, **route depth**, **useful composite paths**, **contradiction rate**, **insight score**, **latency**, **audit completeness**.

## 1. Current state — measurable facts

Captured by direct code inspection on 2026-04-24 (`docs/runs/phase8_ci_baseline.md` for test evidence).

### 1.1 Hex topology

- 8 top-level cells, flat, no sub-cells: `general`, `thermal`, `energy`, `safety`, `seasonal`, `math`, `system`, `learning` (`waggledance/core/hex_cell_topology.py:33-51`).
- Ring-1 adjacency: hardcoded bidirectional `frozenset`s in `_ADJACENCY` (lines 63-72).
- Ring-2 computed dynamically (`_ring2()`, lines 234-242).
- Topology is **immutable after import**. No runtime mutation path.

### 1.2 Solver registry

- `core/symbolic_solver.ModelRegistry` auto-discovers `configs/axioms/*.yaml` on first access (`core/symbolic_solver.py:97-105`).
- Cottage domain: 10 axiom YAMLs under `configs/axioms/cottage/`. Memory notes from 2026-04-23 record 22 solvers across 8 cells after library expansion (commit `72e97a3`); the discrepancy is because not every solver lives under `cottage/` — full count is cross-capsule.
- `reload()` exists (lines 115-118) but is **never called by runtime code** — test scaffolding only.
- **No proposal-merge pathway exists today.** New axioms land by manual YAML + git.

### 1.3 Routing

- `core/smart_router_v2.SmartRouterV2.route()` (lines 120-212) decides in this order: HotCache → capsule key-decision match → keyword classifier → capsule priority fallback.
- `hybrid_retrieval_service` currently plugs in as a HotCache candidate provider (shadow / candidate mode; Phase D-1 is LIVE with `min_score=0.60`). It does **not** pick solvers; it picks a retrieval layer.
- Solver selection is still capsule-layer binding, not hybrid-driven.

### 1.4 Quality gate

- `waggledance/core/learning/quality_gate.py` grades `CaseTrajectory`s as GOLD / SILVER / BRONZE / QUARANTINE.
- Output: `PromotionDecision(auto_promote, monitoring_hours, feeds_specialist, feeds_procedural, reason)`.
- **Mode today is specialist-training only.** There is no axiom-generation mode. The gate is a filter, not a generator.

### 1.5 Dream Mode / nightly learning

- `waggledance/core/learning/dream_mode.py` runs from `night_learning_pipeline.NightLearningPipeline.run_cycle()` during a 22:00-06:00 window.
- It produces synthetic `CaseTrajectory`s (`trajectory_origin="simulated"`, `synthetic=True`) and counterfactual alternatives.
- **It never writes axioms.** It feeds specialist training only, and only bronze-grade so procedural memory is not polluted.
- **No bridge-candidate emission** exists yet.

### 1.6 MAGMA audit

- `core/audit_log.py` + `waggledance/core/magma/audit_projector.py`.
- SQLite append-only (`data/audit_log.db`, WAL mode) with 28 autonomy event types (`goal.*`, `plan.*`, `action.*`, `policy.*`, `capability.selected`, `verification.*`, `world.snapshot_committed`, `specialist.*`, `learning.*`, `reflection.*`, `simulated.*`).
- Coverage is **goal/action/capability-level**, not per-solver-call yet.

### 1.7 EventBus

- `waggledance/bootstrap/event_bus.InMemoryEventBus` only. **No Redis, no NATS, no durability.** Event loss on shutdown.

### 1.8 LLM adapter

- `waggledance/adapters/llm/ollama_adapter.py`. Default model `phi4-mini`. Circuit breaker 3 failures / 30 s recovery. 2 retries. Timeouts 120s / 10s connect.
- **No vLLM adapter.** vLLM references exist only in unused legacy code.

### 1.9 Metrics

- Prometheus `/metrics` exposes 15 counters + 4 gauges (see `waggledance/adapters/http/routes/metrics.py:45-66`): hex-mesh and retrieval routing counters plus `cells_loaded`, `quarantined_cells`, `waggledance_hex_mesh_enabled`, `waggledance_up`.
- **No per-solver-call metrics**, no proposal-gate verdict metrics, no composition-path metrics.

## 2. Measurable capability growth — the definitions we will use

"Capability grows" is shorthand for **observable improvement across one or more of the following signals without regression on the others.**

| Signal | Definition | Source |
|---|---|---|
| `coverage` | fraction of production queries routed to a deterministic solver | MAGMA trace ratio of solver-win vs LLM-fallback |
| `llm_fallback_rate` | 1 - coverage, but measured per-cell | metrics endpoint, per-cell counters |
| `useful_composite_paths` | count of typed solver chains (depth ≥ 2) whose combined value score exceeds either constituent alone | composition graph (Phase 6) |
| `route_depth` | median number of hops from query → final answer | SmartRouterV2 tracing |
| `route_latency_ms` | p50 / p95 of `route()` + solver execution | existing timing, exposed as new metric |
| `contradiction_rate` | fraction of proposals rejected because they conflict with an existing in-cell solver | proposal gate (Phase 5) verdicts |
| `duplicate_solver_rejections` | proposals rejected by hash match | proposal gate |
| `insight_score` | dream-mode projected value of a candidate trajectory | dream mode, already emitted |
| `audit_completeness` | fraction of solver calls with a MAGMA trace | audit log row count / call counter |

Nothing here implies "exponential consciousness." It implies **a scoreboard**. A change to the codebase is only "capability growth" if the scoreboard moves favorably.

## 3. Safe recursive subdivision — plan only, not auto-split

The topology must eventually deepen (one `thermal` cell with 40 solvers is worse than 6 thermal sub-cells with 6-8 solvers each). But runtime must **never mutate the topology autonomously.**

### 3.1 Subdivision triggers (proposed, gated)

`tools/hex_subdivision_plan.py` (Phase 7) reads manifests and composition report and emits subdivision candidates when any hold:

- `solver_count_by_cell` above a configurable threshold
- `cell_gap_score` (open-gap count) above threshold
- `llm_fallback_rate_by_cell` above threshold
- intra-cell semantic entropy too high (measured via embedding cluster dispersion)
- too many valid bridge candidates inside one cell (signals heterogeneous scope)
- repeated proposal-gate rejections because the cell scope is too broad

Output: a candidate plan document with parent cell, proposed sub-cells, reason, expected benefit, risk, rollback plan, tests needed. **Humans decide.** Nothing mutates `hex_cell_topology.py` without a reviewed commit.

### 3.2 Subdivision implementation (future, out of Phase 8 scope)

Actual sub-cell support in `HexCellTopology` requires:

- `cell_id` to be a dotted path (`thermal.heatpump`)
- adjacency lookup to be hierarchical (parent-sibling + same-parent-sub-sibling)
- routing to be aware of depth so a generic `thermal` query doesn't drop into `thermal.heatpump` by accident

This is a separate, later change set. Phase 8 prepares the *planning surface*, not the runtime change.

## 4. Teacher-Claude protocol — structural non-confusion

Claude (or any LLM acting as teacher) is prevented from confusing itself **by not giving it the chance**: it never sees the global library.

### 4.1 Input budget

Teacher input per session is exactly one manifest file:

```
docs/cells/<cell_id>/MANIFEST.yaml
```

The manifest (Phase 2) contains: parent, level, siblings, neighbors, solver list with signatures, top open gaps, top fallback queries, recent rejections, candidate bridge edges, training-pair count, contradiction count, latency stats, LLM fallback rate, manifest hash. Everything the teacher needs — nothing it doesn't.

Typical manifest size target: under 8 KB per cell. Fits easily in a reasoning context.

### 4.2 Output contract

Teacher output is a machine-checkable `solver_proposal.schema.json`-valid YAML/JSON document (Phase 4). Required fields include `provenance_note`, `assumptions`, `invariants`, `expected_failure_modes`, `estimated_latency_ms`, `expected_coverage_lift`, `risk_level`, `tests`.

Rules:
- Teacher proposes 1–3 solvers or one improvement per session.
- Teacher does not modify code.
- Teacher does not invent production metrics.
- Teacher must declare uncertainty.
- Teacher must prefer deterministic solvers over LLM-based solvers.

### 4.3 Gating

A proposal passes through `tools/propose_solver.py` (Phase 5) which applies 12 gates (schema, cell existence, hash dedup, type/unit consistency, deterministic replay, contradiction against existing in-cell solvers, invariants present, tests present, estimated latency under budget, no secrets / absolute paths, no hidden LLM dependency, verdict). Verdicts: `REJECT_SCHEMA`, `REJECT_DUPLICATE`, `REJECT_CONTRADICTION`, `REJECT_LOW_VALUE`, `ACCEPT_SHADOW_ONLY`, `ACCEPT_CANDIDATE`.

Auto-merge is forbidden. A passing proposal is a *candidate*, not a change.

## 5. Quality gates — extended

The existing `quality_gate.py` grades specialist training trajectories. We extend the pattern, not the module.

| Stage | Implementation | Role |
|---|---|---|
| Proposal-time | `tools/propose_solver.py` (Phase 5, new) | Block bad proposals before shadow |
| Shadow-time | routing path with `ACCEPT_SHADOW_ONLY` flag | Measure against live traffic without affecting responses |
| Promotion-time | existing `quality_gate.py` generalised if needed | Decide GOLD/SILVER/BRONZE/QUARANTINE for the candidate |
| Runtime-time | existing circuit breaker + MAGMA | Ensure a bad promotion can be yanked |

A candidate that never earns GOLD stays at SHADOW and is allowed to decay out; no one manually cleans up failed proposals except via retention policy.

## 6. Why durable event bus is required for multi-cell federation

The in-process `InMemoryEventBus` is fine for single-cell reliability. It is **not** fine for:

- Replaying audit events across a restart (events vanish on shutdown).
- Cross-process subscribers (other cells, the hologram, an offline analyzer) receiving events reliably.
- MAGMA trace completeness when a subscriber briefly drops.

### 6.1 Raw Redis Pub/Sub is insufficient

Redis Pub/Sub is fire-and-forget. If a subscriber is down when a message is published, the message is lost. That directly violates the audit-completeness principle. Not acceptable as the bus of record.

### 6.2 Durable options considered

- **Redis Streams** — append-only log with consumer groups and position tracking. Durability is on Redis persistence (RDB/AOF). Good: lightweight, one infrastructure piece, native consumer-group rebalancing. Risk: operational care around AOF fsync policy; acceptable for WaggleDance scale.
- **NATS JetStream** — native durable subject-based streams with ack/redelivery. Good: designed for exactly this; strong ordering per-subject; subscriber replay from any offset. Risk: extra infrastructure; TLS and auth configuration; team unfamiliarity.

Both are valid. Phase 8 **does not pick**. It documents the decision framework and produces a migration stub if implementation is requested. Decision criteria: existing Redis footprint → Redis Streams; greenfield or multi-region → NATS JetStream.

## 7. vLLM as optional hive-generation adapter

Ollama is the current LLM adapter. vLLM is a batched, throughput-optimised serving layer for local GPUs.

- **vLLM does NOT replace solver-first.** The router still picks deterministic solvers first.
- **vLLM role, if added:** batch generation for nightly dream-mode trajectory synthesis, synthetic query generation, candidate-draft writing — workloads where latency is minutes-to-hours and throughput matters more than single-request latency.
- Ollama stays as the runtime fallback path (single-request, low concurrency, existing circuit breaker) because that matches production traffic shape.

Adapter would live at `waggledance/adapters/llm/vllm_adapter.py` with the same port contract. Port is shared; adapters are swappable per call-site via the existing fallback layer plumbing.

## 8. Online learning — shadow / calibration only

Shadow and calibration inference at runtime is acceptable because it affects nothing downstream. Nightly promotion remains the only authoritative path for changing what the production router picks.

In code terms:
- A proposal that passes the gate with `ACCEPT_SHADOW_ONLY` may log candidate outputs to MAGMA under `simulated.*` events.
- No shadow output is composed into the final user response.
- A promotion to GOLD happens during a scheduled learning run, not during a user request.

This keeps runtime deterministic and keeps the audit trail clean.

## 9. Non-goals for Phase 8

- No multi-node runtime federation.
- No automatic topology splitting.
- No runtime axiom mutation.
- No production vLLM path.
- No exponential-capability claim.

These are roadmap items. Phase 8 produces the scaffolding that makes a safe future path possible; it does not walk it.

## 10. Outputs of Phase 8 (what this doc gates)

| Phase | Artifact |
|---|---|
| 2 | `tools/cell_manifest.py`, `tests/test_cell_manifest.py`, `docs/cells/<cell_id>/MANIFEST.yaml` |
| 3 | `waggledance/core/learning/solver_hash.py`, `tools/solver_dedupe.py`, `tests/test_solver_hash.py`, `docs/runs/solver_dedupe_report.md` |
| 4 | `schemas/solver_proposal.schema.json`, `docs/prompts/cell_teacher_prompt.md`, `tests/test_solver_proposal_schema.py` |
| 5 | `tools/propose_solver.py`, `tests/test_propose_solver_gate.py`, `docs/runs/proposal_gate_*.md` |
| 6 | `waggledance/core/learning/composition_graph.py`, `tools/solver_composition_report.py`, `tests/test_composition_graph.py`, `docs/runs/solver_composition_report.md` |
| 7 | `tools/hex_subdivision_plan.py`, `tests/test_hex_subdivision_plan.py`, `docs/runs/hex_subdivision_plan.md` |
| 8 | metrics additions behind existing collector OR `docs/architecture/PHASE8_METRICS.md` + reporting script |
| 9 | `tools/run_honeycomb_400h_campaign.py`, `docs/runs/honeycomb_400h_plan.md` |
| 10 | `docs/runs/phase8_validation.md` |
| 11 | `README.md` section (planned / scaffolding / experimental), `CHANGELOG.md` Unreleased entry |

Every artifact is additive. Nothing in the existing runtime is moved, renamed, or silently changed.
