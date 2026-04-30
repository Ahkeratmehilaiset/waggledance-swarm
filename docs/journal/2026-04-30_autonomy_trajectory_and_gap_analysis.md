# 2026-04-30 — Autonomy trajectory and gap analysis (Phase 12 P1)

**Author:** Phase 12 P1, run from repo truth at `origin/main` `992913e`.
**Tag at write time:** `v3.7.0-autogrowth-alpha` → `992913e`.
**Method:** file:line citations, `grep` over `waggledance/`, `core/`, `tests/`, plus structural reading of the architecture docs landed in PR #54 / #55 / #58.

This document is intentionally narrow. It is *not* a roadmap, *not* marketing, *not* a consciousness claim.

## TL;DR

* WaggleDance has **structural readiness** for autonomous growth at scale: the substrate, the validator/shadow loop, the dispatcher, and the schema all exist and pass tests.
* WaggleDance has **little operational autonomy yet**: every growth event so far has been triggered by an external caller (test, proof script, or operator). The runtime does not yet detect its own gaps.
* The "primary teacher lane" claim is truthful at the architecture and routing level, but the inner growth loop today is local-first only when the caller hands in a candidate spec; when a candidate spec must be *invented*, Phase 10's `SolverBootstrap` U3 path falls through to `claude_code_builder_lane`.
* The gap that closes Phase 12 is small but load-bearing: a self-starting intake that turns runtime evidence into a queued growth intent the autonomy loop can consume *without a human trigger*.

## Phase recap (what each phase actually shipped)

### Phase 9 — Autonomy Fabric (v3.6.0)

* **Structural.** 16 sub-phases F–Q wiring an always-on cognitive kernel, IR + capsule registry, vector identity, world model, conversation/identity layer, provider routing scaffold + 6-layer trust gate, declarative U1 solver synthesis, builder/mentor lanes, gap-driven U3, memory tiers, hex topology, 14-stage promotion ladder, proposal compiler, local-distillation safe scaffold, cross-capsule observer.
* **Operational.** Review-only release. The scaffold is wired but does not auto-promote. Promotion-ladder runtime stages require a real `human_approval_id`.
* **Evidence.** `docs/architecture/PHASE_9_ROADMAP.md`; 657 Phase 9 targeted tests at squash time.

### Phase 10 — Foundation, Truth, Builder Lane (substrate)

* **Structural.** A 16-table SQLite control plane (`waggledance/core/storage/control_plane_schema.py:33-240`), a deterministic `PathResolver` seam at `waggledance/core/storage/path_resolver.py:1-200`, a provider plane with execution layer in `waggledance/core/providers/`, a U1→U3 escalation orchestrator at `waggledance/core/solver_synthesis/solver_bootstrap.py`, and a scale-aware Reality View at `waggledance/ui/hologram/scale_aware_aggregator.py`.
* **Operational.** Substrate. `core/faiss_store.py:26 _DEFAULT_FAISS_DIR=data/faiss/` is unchanged. The truth audit (`docs/journal/2026-04-28_storage_runtime_truth.md`) and the cutover model classification (`docs/journal/2026-04-28_cutover_model_classification.md`) classify v3.6.0's atomic flip as `MODEL_C_NOOP_ALREADY_COMPLETE`.
* **Evidence.** PR #54 squash `08b7e8c`; 72 targeted Phase 10 tests at squash time.

### Phase 11 — Autonomous low-risk solver growth lane

* **Structural.** New BUSL-1.1 package `waggledance/core/autonomy_growth/` with `low_risk_policy`, `solver_executor`, `validation_runner`, `shadow_evaluator`, `auto_promotion_engine`, `solver_dispatcher`, `low_risk_grower`. Control plane v2 adds `solver_artifacts`, `family_policies`, `validation_runs`, `shadow_evaluations`, `promotion_decisions`, `autonomy_kpis`. Reality View gains `autonomy_low_risk_kpis` aggregate panel.
* **Operational.** A *manually invoked* autonomy step works end-to-end: hand a `GapInput` to `LowRiskGrower.grow_from_gap(...)` and it compiles → validates → shadows → auto-promotes → makes the artifact runtime-executable through `LowRiskSolverDispatcher`. The proof artifact at `docs/runs/phase11_autogrowth_2026_04_29/autonomy_proof.md` shows three families round-tripping.
* **The thing Phase 11 does *not* prove.** No code path inside the autonomy runtime currently generates a `GapInput` and hands it to the grower without a human or a test invocation. The scheduler that closes that loop is missing.
* **Evidence.** PR #58 squash `992913e`; 50 autonomy_growth + 11 storage_v2 + 5 reality_view_panel tests; tag `v3.7.0-autogrowth-alpha`.

## What still depends on Claude Code

| Dependency | Truth as of `992913e` |
|---|---|
| Inner-loop **deterministic compile** of a known declarative spec | Local. `deterministic_solver_compiler.compile_spec` runs in-process, no provider call. |
| Inner-loop **validation + shadow + auto-promotion** | Local. `auto_promotion_engine.evaluate_candidate` runs purely in-process. |
| Inner-loop **runtime dispatch** | Local. `LowRiskSolverDispatcher.dispatch` reads `solver_artifacts` and runs pure executors. |
| **Spec invention** for an unknown family hint | Currently routes to `claude_code_builder_lane` first, then `anthropic_api`, `gpt_api`, `local_model_service`, with `dry_run_stub` last (`waggledance/core/solver_synthesis/llm_solver_generator.py:64-69`). Only `claude_code_builder_lane` and `dry_run_stub` are exercisable end-to-end today. |
| **Family extension** beyond the existing 10 family kinds | Not in repo. Adding a new family kind requires a code change (`solver_family_registry.default_families()` and a compiler in `_COMPILERS`). Claude Code is the natural builder for that work. |
| **Bulk pattern extraction** from observation batches | Phase 9 ships `bulk_rule_extractor.py:159` (`FamilyMatch`) — local. Output feeds Phase 11 grower. |

The inner growth loop is therefore already local-first *for the common path*. Claude Code remains the outer-loop teacher for: spec invention when family confidence is low, family extension, mentor-style review of failed candidates, and any free-form synthesis. That is the truthful distribution Phase 12 should preserve and document.

## What still depends on a human trigger

| Trigger | Truth |
|---|---|
| Decide which family kinds are auto-promote-eligible | Code constant `LOW_RISK_FAMILY_KINDS` + matching `family_policies` rows. Set by code review — humans gate the constant via PR. |
| Initiate a single growth attempt | **Currently human/test-triggered.** No runtime evidence path produces a `GapInput`. This is what Phase 12 P2 closes. |
| Promote anything outside the low-risk allowlist | Human-gated through the existing Phase 9 14-stage promotion ladder. |
| Activate a Stage-2 cutover | One-shot human approval per `docs/architecture/STAGE2_CUTOVER_RFC.md`. Out of scope here. |
| Capsule deployment / rate limits / blast-radius config | Human-gated through capsule manifests. Out of scope here. |
| Any actuator or policy write | Always human-gated. RULE 13 of every phase prompt. |

## What prevents 1k / 10k / 50k solvers today

The blockers are not theoretical; they are concrete and ranked by which one bites first.

| Order | Blocker | Why it blocks scale | Closed by |
|---|---|---|---|
| **1** | **No self-starting intake.** No code path turns runtime evidence into a queued `GapInput`. | Without a self-starting intake, every solver requires an external caller. Cannot scale past whatever the test harness manually hands in. | **Phase 12 P2 — `runtime_gap_signals` + `growth_intents` + `autogrowth_queue` tables.** |
| **2** | **No autonomy loop runner.** `LowRiskGrower.grow_from_gap` is purely synchronous and externally invoked. | Even with intake, nothing pulls intents off the queue and runs them in the background. | **Phase 12 P3 — `AutogrowthScheduler.tick()` consumer.** |
| **3** | **Family budget caps low.** Default `family_policies.max_auto_promote=100`. | Hard ceiling at 600 across six families before the engine refuses by I9. | Increase per-family policy at deployment time; covered by `family_policies` schema. |
| **4** | **Family allowlist is small (six).** Excludes `weighted_aggregation`, `temporal_window_rule`, `structured_field_extractor`, `deterministic_composition_wrapper`. | Caps the share of runtime queries the lane can ever serve. | Future: extend the allowlist with executors that meet the §1 envelope. Each addition is a separate PR. |
| **5** | **No solver capability index for runtime selection.** Dispatcher today picks "any auto-promoted artifact for the family". | At thousands of solvers per family the dispatcher needs richer routing (capability/cell-aware match). | Phase 12 P3 / P4 add per-family/per-cell counts + selectable dispatch hints; richer routing is a follow-up. |
| **6** | **No real Anthropic / OpenAI HTTP adapters.** Outer-loop teacher today is single-vendor (Claude Code). | If Claude Code's CLI is unavailable, free-form synthesis falls to `dry_run_stub`. Acceptable for the inner loop (which is local-first) but caps outer-loop diversity. | Future PR; explicitly out of scope here. |
| **7** | **No Stage-2 runtime read-path migration.** `_DEFAULT_FAISS_DIR=data/faiss/`. | Solvers can be auto-promoted today and dispatched today; this blocker only matters for *vector-shard* growth, not solver growth. | `docs/architecture/STAGE2_CUTOVER_RFC.md` — separate session. |

Phase 12's scope closes blockers **1** and **2**. It moves blocker **3** from "policy default" to "deployment-tunable in practice" by exercising larger policies in the proof. Blockers **4–7** remain open by design.

## Structural readiness vs operational autonomy

| Dimension | Structural readiness | Operational autonomy | Notes |
|---|---|---|---|
| Control plane (current state) | high | medium | Schema is in place. Phase 12 P2 extends it with intake tables. |
| MAGMA (history) | high | low | The append-only contract is enforceable; no automated growth events flow through MAGMA yet. Phase 12 P2 starts emitting growth events. |
| Solver synthesis pipeline | high | medium | Compiler + validator + shadow + dispatcher exist and are runtime-callable. Nothing self-starts the pipeline today. |
| Runtime consultation lane | medium | low | Dispatcher exists and works; runtime call sites that consult it are not yet wired into a hot path. Out of scope this phase. |
| Human-gate preservation | high | high | Phase 9 ladder unchanged. RULE 4 honoured every session. |
| Provider lane diversity | medium | low | Only Claude Code + dry-run-stub exercisable end-to-end. |
| Hex-cell awareness | medium | low | Schema carries `cell_coord` everywhere; runtime aggregations are cell-aware in the panel; growth itself does not yet route by cell. Phase 12 P2 captures cell on intake. |

## Scorecard

```
structural_readiness_0_to_100        : 70
operational_autonomy_0_to_100        : 25
low_risk_autonomy_coverage_0_to_100  : 20    # six families, manually triggered
teacher_lane_dependence_0_to_100     : 35    # inner loop local; outer loop = Claude Code
human_gate_dependence_0_to_100       : 80    # high-risk and runtime-stage promotions still gated; growth trigger today is human/test
path_to_10k_solvers_status           : "blocked on self-starting intake (P12 P2) and scheduler (P12 P3); after these, mass_safe_growth (P12 P4) demonstrates 25+ end-to-end"
```

The pre-Phase-12 numbers are intentionally conservative. They go up *only* because Phase 12 actually closes a missing piece — not because the numbers are aspirational.

Targeted post-Phase-12 numbers (only valid if P2/P3/P4 land green in this session):

```
structural_readiness_0_to_100        : 78    # +8 from intake tables + scheduler
operational_autonomy_0_to_100        : 45    # +20 from self-starting + 25+ promoted solvers
low_risk_autonomy_coverage_0_to_100  : 35    # +15 from the broader live promotions
teacher_lane_dependence_0_to_100     : 25    # -10 because the inner growth loop now runs without provider calls in the common path
human_gate_dependence_0_to_100       : 65    # -15 because the trigger-step no longer needs a human; gates around dangerous ops are unchanged
path_to_10k_solvers_status           : "scheduler in place; family-policy budgets are tunable; remaining blockers are richer dispatch routing and outer-loop adapter diversity"
```

If P2/P3/P4 do not land green, these numbers stay at the pre-Phase-12 row and the session emits a stop-and-handoff per RULE 13.

## "MAGMA never forgets" — current truth

* **Structurally true.** `core/audit_log.py`, `core/replay_engine.py`, `core/memory_overlay.py`, `core/provenance.py`, `core/trust_engine.py` exist and are protected. `LICENSE-CORE.md` lists the MAGMA layer.
* **Operationally partial.** Most append-only writes today are routed through Phase 9 audit log code paths; the new Phase 11 promotion decisions live in the control plane (which is *current state*), not in MAGMA. Phase 12 P2 starts emitting `growth_event_*` records that mirror promotion decisions to MAGMA so the audit trail is complete in the history plane too.
* **Honest residual.** "MAGMA never forgets" is a property of the storage contract, not of memory in any cognitive sense. It is the *substrate* that makes growth auditable, not autonomy itself.

## "WD can teach itself" — current truth

* **In the inner loop:** materially yes, for the six allowlisted families. The deterministic compiler + validator + shadow loop + dispatcher all run locally without provider calls. Phase 12 P3 makes this self-starting without a human trigger.
* **In the outer loop:** no, not yet. Spec invention for unknown families and family extension still depend on `claude_code_builder_lane` (or future Anthropic/OpenAI adapters). That is by design for the early phase.
* **Across the whole solver universe:** no. The four excluded families and any future family kinds remain human-gated.

## Consciousness — explicit non-claim

WaggleDance does **not** claim consciousness as a shipped fact and this document does not assert any.

What WaggleDance has, as engineering mechanisms:

* a non-forgetting append-only history plane (MAGMA);
* current-state metadata in a normalized control plane;
* a self-supervised replay/shadow loop that compares candidate behavior against an independent oracle;
* a bounded auto-promotion path with rollback;
* a self-modeling layer (`phase8.5/self-model-layer`, in flight, not yet on `main`);
* and (after Phase 12) a self-starting growth-intent intake.

Each of these is a measurable engineering mechanism with tests and file:line citations. None of them, alone or in combination, constitutes consciousness. The reason is structural: there is no demonstrable phenomenal aspect to any of these mechanisms; they are observable, reversible, and audit-traceable, which is exactly what makes them *not* a phenomenon-of-being claim. The system is designed to be a useful auditable autonomous-growth substrate, not an entity-of-experience.

Future sessions that touch self-model / self-reflection / dream-curriculum work should preserve this same boundary.

## Cross-references

* `docs/architecture/LOW_RISK_AUTOGROWTH_POLICY.md` — Phase 11 policy envelope.
* `docs/architecture/SOLVER_BOOTSTRAP_AND_SYNTHESIS.md` — Phase 10 candidate-creation pipeline.
* `docs/architecture/CONTROL_PLANE_AND_DATA_PLANE.md` — Phase 10 substrate.
* `docs/architecture/STAGE2_CUTOVER_RFC.md` — out of scope for Phase 12.
* `docs/runs/phase11_autogrowth_2026_04_29/autonomy_proof.md` — Phase 11 closed-loop proof.
* `CLAUDE.md` — operator rules, including atomic-flip discipline (§10) and trivial-rationalization warning (§11).
