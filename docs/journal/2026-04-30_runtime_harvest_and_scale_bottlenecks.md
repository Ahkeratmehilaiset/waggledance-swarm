# 2026-04-30 — Runtime harvest and scale bottlenecks (Phase 13 P1)

**Author:** Phase 13 P1, run from repo truth at `origin/main` `d1c3a10`.
**Composes onto:** `docs/journal/2026-04-30_autonomy_trajectory_and_gap_analysis.md` (Phase 12 P1 trajectory).
**Method:** file-level reading of the autonomy_growth + storage + ui_hologram code, plus the Phase 11/12 proof artifacts. No marketing.

## TL;DR

Phase 12 made the autonomy loop **self-starting** — a synthetic gap-detector script can drive 30 promotions end-to-end with 0 provider calls. Phase 13 has to take that seam and connect it to the *real* runtime query path, so signals are emitted by genuine query traffic, not by a script that hand-builds GapSignal objects.

There are three concrete bottlenecks Phase 12 left behind, all of which a runtime-traffic operator would notice within minutes:

1. **No runtime call path emits signals.** `RuntimeGapDetector.record(GapSignal)` exists, but no module in `waggledance/core/` invokes it. The mass proof (`tools/run_mass_autogrowth_proof.py`) hand-constructs GapSignal objects with already-populated `spec_seed` payloads. That is structural readiness, not operational autonomy.
2. **The dispatcher only finds solvers by family + insertion order.** `LowRiskSolverDispatcher._select_artifact` either takes a caller-supplied `solver_name` (exact-name lookup) or returns the most recently registered auto-promoted artifact for the family. With 100+ promoted solvers, "any qualified solver wins" stops being meaningful: each query gets the latest-inserted solver regardless of whether it actually solves the query's structured shape.
3. **Seed derivation has no shared library.** Each proof script invents its own canonical seeds. There is no `low_risk_seed_library` that turns a harvested signal into a valid SolverSpec seed for the existing six families. Without that, harvested signals cannot scale into many candidates cheaply.

These three are the right gaps to close in Phase 13. The result must be measured on the *real* runtime path (a router that emits signals automatically + a capability-aware dispatch path), not on synthetic scripts.

## What Phase 12 actually shipped

* **Schema v3** with `runtime_gap_signals`, `growth_intents`, `autogrowth_queue`, `autogrowth_runs`, `growth_events` plus 13 indexes. Forward-only migration. Reads as designed.
* **Self-starting intake** at `waggledance/core/autonomy_growth/gap_intake.py`: `RuntimeGapDetector.record(GapSignal)` and `digest_signals_into_intents`. Both write to control plane. `growth_events` mirrors the audit trail.
* **Scheduler** at `waggledance/core/autonomy_growth/autogrowth_scheduler.py`: `AutogrowthScheduler.run_until_idle()`. Atomic claim path, terminal-on-rejection, scheduler-id parallel-safety, idempotent enqueue.
* **Family reference oracles** at `waggledance/core/autonomy_growth/family_oracles.py`. Six independent reference implementations.
* **Mass-safe proof at scale**: 30 deterministic candidates, 6 families, 8 hex cells, 0 errors, 0 provider calls. The `provider_jobs` empty invariant is locked by `tests/autonomy_growth/test_outer_inner_loop_truthful.py::test_mass_autogrowth_zero_provider_calls`.
* **Reality View `autonomy_self_starting_kpis` panel** alongside Phase 11's `autonomy_low_risk_kpis`.

What this materially changed: a script can now drive 30 promotions without a human and without a provider call. What it did *not* change: real query traffic in the autonomy runtime still has no path through this loop.

## What is still synthetic / proof-only after Phase 12

| Claim | Truth |
|---|---|
| "WD self-starts growth from runtime evidence." | The script `tools/run_mass_autogrowth_proof.py` self-starts 30 promotions. **No `waggledance/core/` module emits a `runtime_gap_signal` automatically.** A real query that misses still does nothing. |
| "Auto-promoted solvers serve runtime queries." | `LowRiskSolverDispatcher.dispatch(family_kind, inputs)` works at the unit-test level. **No runtime module calls it.** The runtime never actually consults an auto-promoted solver in production today. |
| "The lane is local-first." | True at the unit/proof level. Inner loop has zero provider calls. **But "inner loop" is a synthetic script today, not the runtime.** |
| "30 solvers grown at scale." | True for one script run. **Each script run hand-builds the seeds.** Without a seed library + harvest pipeline, the next run repeats the same seeds, not new ones. |

The numbers in the Phase 12 scorecard are true under one specific test harness. They are *not* true if you point at the real runtime and ask "did WD just learn from that traffic?". Phase 13 has to fix that.

## Where exact-name dependence still lives

`waggledance/core/autonomy_growth/solver_dispatcher.py` line ~155 (`_select_artifact`):

```python
if query.solver_name is not None:
    # exact-name lookup
    ...
artifacts = self._cp.list_auto_promoted_artifacts_for_family(
    query.family_kind, limit=1
)
return artifacts[0] if artifacts else None
```

Two paths:
* `solver_name` is supplied → exact-name lookup. Useful for tests, useless at runtime where the caller does not know the solver name.
* No `solver_name` → "most recently registered auto-promoted artifact for the family". With one solver per family this is fine. With 100 solvers per family this is roulette: query for `lookup_table` returns whichever lookup table was last promoted, regardless of which key/domain the query actually concerns.

This is the second concrete bottleneck. Phase 13 must add capability-aware dispatch — match by structured features (e.g. for `scalar_unit_conversion`: `from_unit`+`to_unit`; for `lookup_table`: domain + key set; for `threshold_rule`: subject field + operator), not by insertion order.

## What blocks 100 / 1000 / 10000 deterministic solvers

Re-ranked from the Phase 12 trajectory journal, with what's already done crossed out:

| # | Blocker | Status | Closes how |
|---|---|---|---|
| ~~1~~ | ~~No self-starting intake~~ | **closed in Phase 12** (schema + detector + digest) | — |
| ~~2~~ | ~~No autonomy loop runner~~ | **closed in Phase 12** (`AutogrowthScheduler`) | — |
| **1** | **No runtime call path emits signals.** Real query traffic does not flow through the harvest seam. | OPEN | **Phase 13 P2 — `runtime_query_router.py` invokes `RuntimeGapDetector` on miss/fallback.** |
| **2** | **Dispatcher routes by insertion order, not capability.** With many promoted solvers per family, the wrong one is returned. | OPEN | **Phase 13 P3 — schema v4 `solver_capability_features` + `dispatch_by_features`.** |
| **3** | **No shared seed library.** Each proof rebuilds its seeds; harvested signals cannot scale into many candidates without manual seed crafting. | OPEN | **Phase 13 P4 — `low_risk_seed_library.py` with canonical templates + parameter sweeps + per-family feature extractors.** |
| 4 | Family budget caps low (default 100 per family). | Tunable | Per-deployment policy update. |
| 5 | Family allowlist is small (six). | By design | Each addition is a separate PR; not in scope here (RULE 19). |
| 6 | No real Anthropic / OpenAI HTTP adapters. | OPEN | Future PR; outer-loop matter; explicitly out of scope here. |
| 7 | No Stage-2 runtime read-path migration. | OPEN | `STAGE2_CUTOVER_RFC.md`; not in scope here (RULE 17). |

Phase 13's closure of bottlenecks 1, 2, 3 makes the path to 100+ solvers operational under bounded assumptions: a runtime-traffic harness that produces structured query envelopes will, after one harvest cycle, cause a meaningful share of the same query class to be served by auto-promoted solvers through the normal runtime path.

## Where Claude Code is and is not in the loop

**Inner loop, common path (after Phase 13):**
* runtime call → router → built-in solver (if applicable) → capability-aware auto-promoted solver lookup → dispatch hit → done. **No provider call.**
* on miss → emit `runtime_gap_signal` automatically → digest → enqueue → scheduler tick → grower → promote → next time, dispatcher serves it. **No provider call in the common path.**

**Outer loop (unchanged):**
* family extension (adding a new low-risk family kind) — code change → PR → human review → Claude Code Opus 4.7 mediated;
* spec invention when the seed library cannot derive a valid seed locally (rare today, common in future as harvest broadens) — provider plane → `claude_code_builder_lane`;
* mentor review of failed candidates (analyzing rejection patterns) — provider plane → `claude_code_builder_lane`;
* refactoring / hardening of the lane itself — same.

The Phase 13 regression test will assert that the **runtime-harvest proof** common path writes zero `provider_jobs` rows. If a future change quietly routes the inner loop through a paid provider, the test fails.

## What still depends on human gates

Unchanged from Phase 12. Restating for clarity:

| Trigger | Truth |
|---|---|
| Decide which family kinds are auto-promote-eligible | Code constant `LOW_RISK_FAMILY_KINDS` + `family_policies` rows. Set by code review — humans gate the constant via PR. |
| Promote anything outside the low-risk allowlist | Human-gated through the existing Phase 9 14-stage promotion ladder. |
| Activate a Stage-2 cutover | `STAGE2_CUTOVER_RFC.md`. Out of scope here. |
| Capsule deployment / rate limits / blast-radius config | Human-gated. |
| Any actuator / policy / production runtime config write | Always human-gated. RULE 10 of every prompt. |

What changes: the *trigger* for individual low-risk growth attempts. Today (Phase 12) synthetic. After Phase 13, real query traffic.

## "MAGMA never forgets" — current truth (post-Phase-12, pre-Phase-13)

* **Structurally true.** AuditLog / ReplayEngine / MemoryOverlay / Provenance / TrustEngine remain protected and append-only by contract.
* **Operationally partial in this lane.** Phase 12 ships `growth_events` as the autonomy lane's history-plane mirror inside the control-plane SQLite — append-only by convention (engine writes only INSERT). It is not the same physical store as MAGMA, but it is the same *contract*: history of growth, not current state.
* **Phase 13 makes this stronger.** When the runtime path emits signals automatically, every query that contributed to a growth event has a `growth_events` row with `event_kind='signal_recorded'` and a payload pointing at the structured runtime query. That is the real audit trail of what happened, why it happened, and what was promoted as a result. The history plane carries the autonomy's growth memory.

What "MAGMA never forgets" still does *not* mean: it does not constitute a memory in any cognitive sense; it is an append-only data structure. Saying it never forgets is a contract claim about the storage layer.

## "WD can teach itself" — current truth

* **Inner loop, after Phase 13:** materially yes for the six allowlisted families *under the structured query classes the harvest seam covers*. The deterministic compiler + validator + shadow loop + dispatcher all run locally without provider calls. The runtime emits signals automatically. The seed library expands harvested signals into many candidates locally.
* **Outer loop:** still teacher-assisted. Spec invention for unknown patterns and family extension still depend on `claude_code_builder_lane`. By design.
* **Across the whole solver universe:** still no. The four excluded families and any future family kinds remain human-gated.

## Consciousness — explicit non-claim (still)

WaggleDance does **not** claim consciousness as a shipped fact. Phase 13 does not change that.

What Phase 13 adds, as engineering mechanisms:

* a real seam where runtime evidence flows into the autonomy loop;
* a capability-aware dispatcher that lets harvested structure stay useful at scale;
* a shared seed library that lets one signal expand into many candidate specs locally.

Each of these is observable, reversible, and audit-traceable. None of them, alone or in combination, constitutes consciousness. Future sessions touching self-model / self-reflection / dream-curriculum work should preserve this same boundary.

## Scorecard — pre-Phase-13 (current truth)

```
structural_readiness_0_to_100        : 78
operational_autonomy_0_to_100        : 45
low_risk_autonomy_coverage_0_to_100  : 35
runtime_harvest_readiness_0_to_100   : 15   # the schema is in place; nothing in waggledance/core/ emits signals
capability_lookup_readiness_0_to_100 : 10   # exact-name + family-FIFO only
teacher_lane_dependence_0_to_100     : 25   # inner loop already zero in synthetic proof
human_gate_dependence_0_to_100       : 65
path_to_100_solvers_status           : "blocked on runtime harvest (P2) and capability lookup (P3); after these, seed expansion (P4) makes 100+ tractable"
path_to_1000_solvers_status          : "blocked on (1) richer seed coverage from harvest, (2) richer per-family feature dimensions in capability lookup, (3) dispatcher caching at scale"
```

## Scorecard — targeted post-Phase-13 (only valid if P2/P3/P4/P5 land green)

```
structural_readiness_0_to_100        : 84   # +6 from runtime seam + capability features
operational_autonomy_0_to_100        : 60   # +15 from real harvest path
low_risk_autonomy_coverage_0_to_100  : 50   # +15 from broader live promotions in proof corpus (48+)
runtime_harvest_readiness_0_to_100   : 60   # +45; seam + automatic emission + dedup; scale-safe but narrow scope
capability_lookup_readiness_0_to_100 : 55   # +45; feature-indexed dispatch for the six families
teacher_lane_dependence_0_to_100     : 20   # -5; inner loop now exercised through real runtime path
human_gate_dependence_0_to_100       : 55   # -10; trigger-step gone, allowlist still gated
path_to_100_solvers_status           : "operationally tractable; the runtime-harvest proof produces 48+ solvers in one cycle"
path_to_1000_solvers_status          : "next phase needs broader harvest scope + richer feature dimensions + per-cell dispatch routing"
```

These targets do not move unless the implementation phases pass. If they do not, Phase 13 emits a stop-and-handoff per RULE 13.

## Cross-references

* `docs/architecture/LOW_RISK_AUTOGROWTH_POLICY.md` — Phase 11 policy envelope; unchanged.
* `docs/architecture/PROVIDER_PLANE_AND_BUILDER_LANES.md` — inner / outer loop distinction (Phase 12 update).
* `docs/runs/phase11_autogrowth_2026_04_29/autonomy_proof.md` — Phase 11 closed loop (3 solvers, manual).
* `docs/runs/phase12_self_starting_autogrowth_2026_04_30/autonomy_proof.md` — Phase 12 self-starting (30 solvers, synthetic harness).
* `tests/autonomy_growth/test_outer_inner_loop_truthful.py` — locks the zero-provider-call inner-loop claim; Phase 13 must extend it to the runtime path.
* `CLAUDE.md` — operator rules, atomic-flip discipline (§10) and trivial-rationalization warning (§11) preserved.
