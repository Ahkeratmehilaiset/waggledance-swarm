# Phase 13 — Runtime Harvest Proof: Real Runtime Path → Capability Uptake

**Generated:** 2026-04-30 by `tools/run_runtime_harvest_proof.py`
**Machine-readable artifact:** [`runtime_harvest_proof.json`](runtime_harvest_proof.json)
**Branch:** `phase13/runtime-harvest-capability-uptake` (off `origin/main` @ `d1c3a10`)
**Composes with:** Phase 11 manual-trigger proof + Phase 12 self-starting proof.

## What this proof shows

The first proof in the WaggleDance autonomy lineage that exercises the **real runtime seam**. Phase 12 used a script that hand-built `GapSignal` objects; Phase 13 routes a corpus of 68 structured runtime queries through `RuntimeQueryRouter.route(...)` exactly the way a real runtime call site would, then re-routes the same queries after one harvest cycle.

```
PASS 1 (before harvest)
   68 structured runtime queries
   → RuntimeQueryRouter.route(query)  ← runtime seam
       → LowRiskSolverDispatcher (capability-aware first, family-FIFO fallback)
       → no auto-promoted solvers exist yet → MISS
       → router emits one runtime_gap_signal per query class (deduped)
       → growth_event mirror written
   Result: 0 served, 68 misses, 68 signals emitted automatically.

HARVEST CYCLE
   digest_signals_into_intents → 68 growth_intents created, 68 enqueued
   AutogrowthScheduler.run_until_idle
       → 68 candidates auto-promoted, 0 rejected, 0 errored
       → solver_artifacts + validation_runs + shadow_evaluations +
          promotion_decisions + solver_capability_features written
   Result: 68 promotions, 120 capability-feature rows.

PASS 2 (after harvest)
   Same 68 structured runtime queries
   → RuntimeQueryRouter.route(query)
       → LowRiskSolverDispatcher.dispatch_by_features(family, features, inputs)
           ← schema-v4 indexed lookup on solver_capability_features
       → matched solver executes pure artifact, returns output
   Result: 68 served via capability lookup, 0 misses.
```

No human trigger. No HUMAN_APPROVAL. **Zero `provider_jobs` rows. Zero `builder_jobs` rows.** Built-in authoritative solvers untouched. Stage-2 cutover not executed.

## Headline numbers

| metric | value |
|---|---:|
| corpus size | 68 |
| pass 1 (before): served | 0 |
| pass 1: fallback / miss | 68 |
| pass 1: signals emitted automatically | 68 |
| harvest: intents created | 68 |
| harvest: intents enqueued | 68 |
| harvest: scheduler drained | 68 |
| harvest: auto-promoted | 68 |
| harvest: rejected | 0 |
| harvest: errored | 0 |
| pass 2 (after): served | 68 |
| **pass 2: served via capability lookup** | **68** |
| pass 2: fallback / miss | 0 |
| `solver_capability_features` rows | 120 |
| `growth_events` total | 272 |
| `provider_jobs` total | **0** |
| `builder_jobs` total | **0** |

## Per-family served (after harvest)

| family | served | seed library size |
|---|---:|---:|
| `scalar_unit_conversion` | 20 | 20 |
| `lookup_table` | 12 | 12 |
| `threshold_rule` | 12 | 12 |
| `interval_bucket_classifier` | 8 | 8 |
| `linear_arithmetic` | 8 | 8 |
| `bounded_interpolation` | 8 | 8 |

**Every seed in the canonical library is served via capability-aware lookup after one harvest cycle.**

## Per-cell served (after harvest)

| cell | served |
|---|---:|
| `system` | 13 |
| `thermal` | 12 |
| `general` | 11 |
| `energy` | 10 |
| `math` | 9 |
| `seasonal` | 6 |
| `safety` | 4 |
| `learning` | 3 |

Eight distinct hex cells covered. Cell coverage is a function of the seed library distribution; the dispatcher does not yet route by cell (deferred to a follow-up that uses `cell_coord` as a feature).

## Capability lookup, not family-FIFO

Phase 12's dispatcher returned *the most recently registered* auto-promoted solver per family. With 20 `scalar_unit_conversion` solvers in the library, every Celsius-to-Kelvin runtime query would have served the wrong solver under that v1 routing.

Phase 13's dispatcher uses `find_auto_promoted_solvers_by_features` against `solver_capability_features` — an indexed table populated at promotion time by the engine's `extract_features(family, spec)` call. For `scalar_unit_conversion` the features are `{from_unit, to_unit}`; for `lookup_table` they are `{domain, default_present}`; for `threshold_rule` they are `{subject, operator}`; etc. The runtime query carries the same feature shape, so the dispatcher finds the matching solver by structured equality. Every seed in the proof corpus is recovered correctly.

## Inner / outer loop boundary

Phase 13's regression test extends Phase 12's invariant:

* `tests/autonomy_growth/test_runtime_harvest_proof_smoke.py::test_runtime_harvest_proof_zero_provider_calls` — runs this proof and asserts `provider_jobs_total == 0` and `builder_jobs_total == 0`.
* `tests/autonomy_growth/test_outer_inner_loop_truthful.py::test_outer_loop_teacher_lane_remains_claude_code_first` — locks `claude_code_builder_lane` at position 1 of the LLM solver generator's `_DEFAULT_PRIORITY_LIST`. Outer loop = teacher lane.

The inner growth loop **operating on the real runtime seam** is now demonstrably zero-provider for the entire seed library.

## Truthfulness boundary — what this proof does NOT show

* **Not Stage-2 cutover.** `core/faiss_store.py:26 _DEFAULT_FAISS_DIR=data/faiss/` unchanged. `STAGE2_CUTOVER_RFC.md` still gates that mechanism.
* **Not full autonomy across the solver universe.** Only six allowlisted families. The four excluded families remain human-gated through the Phase 9 promotion ladder.
* **Not a replacement for the Phase 9 14-stage promotion ladder.** Auto-promotion in this lane sets `solvers.status='auto_promoted'` — a separate flag. Runtime-stage promotions still require `human_approval_id`.
* **Not a runtime *call site* yet.** The `RuntimeQueryRouter` is a real runtime seam; in this PR it is exercised by the proof and tests. Wiring it into a specific runtime call site (e.g. a hot path inside the autonomy runtime) is the next session's work — and explicitly out of scope under RULE 13 if the seam itself is not real first.
* **Not real Anthropic / OpenAI HTTP adapters.** Only `dry_run_stub` and `claude_code_builder_lane` are exercisable. Phase 13 does not change that.
* **No actuator-side autonomy.** Auto-promoted solvers are *consultable*, not *authoritative*. Built-in solvers retain precedence by contract.
* **No consciousness claim.** Self-starting growth driven by runtime evidence is an engineering mechanism — observable, reversible, audit-traceable. It does not constitute a phenomenon-of-experience claim.

## Reproducing the proof

```
python tools/run_runtime_harvest_proof.py
```

Default outputs:

* `docs/runs/phase13_runtime_harvest_2026_04_30/runtime_harvest_proof.json` — committed canonical machine-readable evidence.
* `docs/runs/phase13_runtime_harvest_2026_04_30/runtime_harvest_proof.db` — scratch SQLite, regenerated each run; gitignored via the repo's `*.db` rule.

The script accepts `--out-dir` and `--db` to redirect outputs.

## Cross-references

* `docs/architecture/LOW_RISK_AUTOGROWTH_POLICY.md` — Phase 11 policy envelope this proof exercises.
* `docs/architecture/PROVIDER_PLANE_AND_BUILDER_LANES.md` — inner / outer loop distinction (Phase 12 + Phase 13 updates).
* `docs/runs/phase11_autogrowth_2026_04_29/autonomy_proof.md` — Phase 11 closed-loop proof (3 solvers, manual).
* `docs/runs/phase12_self_starting_autogrowth_2026_04_30/autonomy_proof.md` — Phase 12 self-starting proof (30 solvers, synthetic harness).
* `docs/journal/2026-04-30_runtime_harvest_and_scale_bottlenecks.md` — Phase 13 P1 trajectory + scorecard.
* `tests/autonomy_growth/test_runtime_query_router.py` — runtime seam unit tests.
* `tests/autonomy_growth/test_dispatch_by_features.py` — capability-aware dispatch tests.
* `tests/autonomy_growth/test_runtime_harvest_proof_smoke.py` — locks the before/after invariants.
