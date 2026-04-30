# Phase 11 — Autonomy Proof: Low-Risk Autogrowth Vertical Slice

**Generated:** 2026-04-30 by `tools/run_autonomy_proof.py`
**Machine-readable artifact:** [`autonomy_proof.json`](autonomy_proof.json)
**Branch:** `phase11/autonomous-low-risk-growth-lane` (off `origin/main` @ `30dc1da`)

## What this proof shows

A real, end-to-end no-human path that takes three independent gaps — one each from three different allowlisted families — through:

```
gap (need + spec hint)
  → make_spec (Phase 9 validator)
  → compile_spec (Phase 9 deterministic compiler)
  → ValidationRunner (Phase 11)
  → ShadowEvaluator (Phase 11, oracle = independent reference)
  → AutoPromotionEngine.evaluate_candidate (Phase 11)
       I1..I9 invariants — all pass
  → control plane writes (atomic):
       solvers.status='auto_promoted'
       solver_artifacts row
       validation_runs row
       shadow_evaluations row
       promotion_decisions row (decided_by='autopromotion_engine_v1')
  → LowRiskSolverDispatcher.dispatch
       runtime executor returns the promoted output
  → autonomy_kpis snapshot
```

No human approval. No paid provider call. Built-in authoritative solvers untouched. Atomic flip not executed.

## Three gaps, three families

| family_kind | solver_name | cell | validation cases | shadow samples | accepted | val pass | shadow agree |
|---|---|---|---:|---:|---|---|---|
| `scalar_unit_conversion` | `celsius_to_kelvin_v1` | `thermal` | 5 | 40 | ✅ | 1.0 | 1.0 |
| `threshold_rule` | `hot_threshold_30c_v1` | `thermal` | 4 | 100 | ✅ | 1.0 | 1.0 |
| `lookup_table` | `color_to_action_v1` | `general` | 4 | 10 | ✅ | 1.0 | 1.0 |

Oracles in this run are independent Python reimplementations of each family's mathematical formula (in `tools/run_autonomy_proof.py`), running outside the deterministic compiler and the executor. A 1.0 agreement rate means the artifact's output matched the reference for every shadow sample.

## Runtime path proof — dispatcher hits

After auto-promotion, the runtime dispatcher served all six probe queries from the auto-promoted lane:

| query family_kind | inputs | matched | output | solver |
|---|---|---|---|---|
| `scalar_unit_conversion` | `{"x": 0.0}` | ✅ | `273.15` | `celsius_to_kelvin_v1` |
| `scalar_unit_conversion` | `{"x": 100.0}` | ✅ | `373.15` | `celsius_to_kelvin_v1` |
| `threshold_rule` | `{"x": 50}` | ✅ | `"hot"` | `hot_threshold_30c_v1` |
| `threshold_rule` | `{"x": 10}` | ✅ | `"cool"` | `hot_threshold_30c_v1` |
| `lookup_table` | `{"key": "red"}` | ✅ | `"stop"` | `color_to_action_v1` |
| `lookup_table` | `{"key": "blue"}` | ✅ | `"unknown"` | `color_to_action_v1` |

These calls go through `LowRiskSolverDispatcher` reading `solver_artifacts` from the control plane (no JSON-on-disk system-of-record, RULE 10) and executing through the pure executors in `waggledance/core/autonomy_growth/solver_executor.py`.

## KPI snapshot

| KPI | value |
|---|---:|
| `candidates_total` | 3 |
| `auto_promotions_total` | 3 |
| `rejections_total` | 0 |
| `rollbacks_total` | 0 |
| `validation_runs_total` | 3 |
| `shadow_evaluations_total` | 3 |
| `dispatcher_hits_total` | 6 |
| `dispatcher_misses_total` | 0 |
| `per_family_dispatcher_hits` | `{"lookup_table": 2, "scalar_unit_conversion": 2, "threshold_rule": 2}` |

Persisted as a row in `autonomy_kpis` with `snapshot_at` equal to the current UTC ISO timestamp.

## Truthfulness boundary — what this proof does NOT show

* **Not Stage-2 cutover.** No `runtime_path_bindings` row was flipped. `core/faiss_store.py:26 _DEFAULT_FAISS_DIR` is unchanged. The Stage-2 cutover RFC at `docs/architecture/STAGE2_CUTOVER_RFC.md` still gates that mechanism; this PR does not execute it.
* **Not full autonomy.** Only six allowlisted families are auto-promote-eligible. The four excluded families (`weighted_aggregation`, `temporal_window_rule`, `structured_field_extractor`, `deterministic_composition_wrapper`) still flow through the existing Phase 9 promotion ladder and human gate.
* **Not a substitute for the Phase 9 14-stage promotion ladder.** Auto-promotion in this lane is a *separate status flag* (`solvers.status='auto_promoted'`). The runtime stages of the Phase 9 ladder (post-campaign-runtime-candidate / canary-cell / limited-runtime / full-runtime) still require a real `human_approval_id`.
* **Not an actuator-side change.** The autonomy runtime did not gain any new ability to write to actuators, policies, or production runtime configuration. Auto-promoted low-risk solvers are *consultable*, not *authoritative*.
* **Not real Anthropic / OpenAI HTTP adapters.** The Phase 10 statement holds: only `dry_run_stub` and `claude_code_builder_lane` are exercisable end-to-end today. This proof does not invoke any provider; it operates fully on the deterministic compiler + executors.

## Reproducing the proof

```
python tools/run_autonomy_proof.py
```

Default output paths:

* `docs/runs/phase11_autogrowth_2026_04_29/autonomy_proof.json` — committed as canonical machine-readable evidence.
* `docs/runs/phase11_autogrowth_2026_04_29/proof_control_plane.db` — scratch SQLite file, regenerated each run; gitignored via the repo's `*.db` rule.

The script accepts `--out-dir` and `--db` to redirect outputs.

## Cross-references

* `docs/architecture/LOW_RISK_AUTOGROWTH_POLICY.md` — policy envelope this proof exercises.
* `docs/architecture/CONTROL_PLANE_AND_DATA_PLANE.md` — substrate this proof reads/writes.
* `docs/architecture/SOLVER_BOOTSTRAP_AND_SYNTHESIS.md` — Phase 10 candidate-creation pipeline that this lane consumes.
* `docs/architecture/PROVIDER_PLANE_AND_BUILDER_LANES.md` — preferred provider chain (Claude Code Opus 4.7 first).
* `docs/architecture/STAGE2_CUTOVER_RFC.md` — explicitly out of scope for this proof.
* `tests/autonomy_growth/` — the targeted suite that locks every step of this loop down (110+ green tests including 13 auto-promotion engine cases).
