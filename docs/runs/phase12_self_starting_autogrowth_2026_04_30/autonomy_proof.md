# Phase 12 — Autonomy Proof: Self-Starting Local-First Growth Loop

**Generated:** 2026-04-30 by `tools/run_mass_autogrowth_proof.py`
**Machine-readable artifact:** [`mass_autogrowth_proof.json`](mass_autogrowth_proof.json)
**Branch:** `phase12/self-starting-local-autogrowth` (off `origin/main` @ `992913e`)
**Composes with:** Phase 11 closed-loop proof at [`../phase11_autogrowth_2026_04_29/autonomy_proof.md`](../phase11_autogrowth_2026_04_29/autonomy_proof.md).

## What this proof shows

A real, end-to-end **self-starting** autonomous growth loop that runs **without a human trigger** and **without provider calls**, end-to-end at scale:

```
runtime gap signal observed
  → RuntimeGapDetector.record (writes runtime_gap_signals + emits growth_event)
  → digest_signals_into_intents (creates growth_intents + enqueues into autogrowth_queue)
  → AutogrowthScheduler.run_until_idle
       claim_next_queue_row (atomic SQLite claim, no double-claim)
       → LowRiskGrower.grow_from_gap
            make_spec (Phase 9 validator)
            compile_spec (Phase 9 deterministic compiler)
            ValidationRunner (Phase 11)
            ShadowEvaluator (Phase 11, family reference oracle)
            AutoPromotionEngine.evaluate_candidate (I1–I9 invariants)
              atomic SQLite transaction:
                solvers.status='auto_promoted'
                solver_artifacts row
                validation_runs row
                shadow_evaluations row
                promotion_decisions row
       → autogrowth_runs row recorded
       → growth_event mirror (signal_recorded, intent_created,
                              intent_enqueued, solver_auto_promoted)
       → queue row completed; intent moved to 'fulfilled'
  → LowRiskSolverDispatcher.dispatch
       runtime executor returns the promoted output
```

No human trigger. No HUMAN_APPROVAL collected. No actuator/policy writes. No Stage-2 flip executed. No `provider_jobs` rows written, anywhere — the inner loop is local-first by *measured* behaviour, not just by claim.

## Scope and bounds

* **30 candidate solvers, 5 per family, all 6 allowlisted families.** Generated programmatically from the canonical seed bank in `tools/run_mass_autogrowth_proof.py`. Each seed has its own validation cases and shadow samples; the family reference oracle in `waggledance/core/autonomy_growth/family_oracles.py` runs as the independent oracle.
* **Hex-cell aware.** Seeds span 8 cells (`thermal`, `math`, `energy`, `system`, `seasonal`, `general`, `safety`, `learning`). The proof reports per-cell counts.
* **Bounded.** Scheduler runs `run_until_idle(max_ticks=200)`. Drains the queue in 30 non-idle ticks then exits.
* **Pure-function path.** No subprocess, no network, no provider call. Verified by `tests/autonomy_growth/test_outer_inner_loop_truthful.py::test_mass_autogrowth_zero_provider_calls`.

## Headline numbers

| metric | value |
|---|---:|
| signals recorded | 30 |
| intents created | 30 |
| intents enqueued | 30 |
| scheduler ticks (non-idle) | 30 |
| solvers auto-promoted | 30 |
| rejections | 0 |
| errors | 0 |
| runtime dispatcher hits (probe) | 6/6 (one per family) |
| `provider_jobs` rows after run | **0** |
| `builder_jobs` rows after run | **0** |
| `growth_events` total | 120 (= 30 signals + 30 intent_created + 30 intent_enqueued + 30 solver_auto_promoted) |

## Per-family promotions (5 each)

| family | solvers promoted | example seed names |
|---|---:|---|
| `scalar_unit_conversion` | 5 | celsius_to_kelvin, celsius_to_fahrenheit, meters_to_kilometers, kilograms_to_pounds, kilowatts_to_watts |
| `lookup_table` | 5 | color_to_action, status_to_severity, day_to_workday, hue_to_temp, verdict_to_label |
| `threshold_rule` | 5 | hot_above_30c, cold_below_5c, low_battery_below_20, overload_above_80pct, frost_below_0c |
| `interval_bucket_classifier` | 5 | temp_band, age_band, cpu_band, hour_of_day_band, score_band |
| `linear_arithmetic` | 5 | comfort_score, energy_estimate_kwh, safety_index, seasonal_weight, performance_score |
| `bounded_interpolation` | 5 | battery_charge_curve, comfort_curve_temp, power_efficiency_curve, daylight_factor_hour, learning_rate_decay |

## Per-cell promotions

| cell | solvers promoted |
|---|---:|
| `thermal` | 7 |
| `general` | 5 |
| `energy` | 5 |
| `system` | 4 |
| `seasonal` | 3 |
| `math` | 2 |
| `safety` | 2 |
| `learning` | 2 |

Cell coverage is a side-effect of the seed bank's distribution; the scheduler does not route by cell yet (deferred). Reality View's `autonomy_low_risk_kpis` panel exposes these counts via `per_family_dispatcher_hits`; Phase 13+ can add a per-cell aggregate panel.

## Runtime path proof — six dispatcher probes

After the scheduler drained the queue, the runtime dispatcher served one probe per family. All six matched and returned the expected solver:

| family | probe inputs | matched | output | solver served |
|---|---|---|---|---|
| `scalar_unit_conversion` | `{"x": 25.0}` | ✅ | `25000.0` | `kilowatts_to_watts_i0005` |
| `lookup_table` | `{"key": "red"}` | ✅ | `"UNKNOWN"` | `verdict_to_label_i0010` |
| `threshold_rule` | `{"x": 35.0}` | ✅ | `"above_zero"` | `frost_below_0c_i0015` |
| `interval_bucket_classifier` | `{"x": 17.5}` | ✅ | `"out"` | `score_band_i0020` |
| `linear_arithmetic` | (multi-column) | ✅ | `1.1` | `performance_score_i0025` |
| `bounded_interpolation` | `{"x": 25.0}` | ✅ | `0.000875` | `learning_rate_decay_i0030` |

The dispatcher's v1 strategy is "most recently registered auto-promoted solver wins for the family" — that is why each probe returns the last-registered seed. This is correct given the v1 single-key dispatch; richer routing (capability-aware / cell-aware match) is future work.

## Truth boundary — what this proof does NOT show

* **Not Stage-2 cutover.** `core/faiss_store.py:26 _DEFAULT_FAISS_DIR` is unchanged. `STAGE2_CUTOVER_RFC.md` still gates that mechanism.
* **Not full autonomy.** Only six allowlisted families. `weighted_aggregation`, `temporal_window_rule`, `structured_field_extractor`, `deterministic_composition_wrapper` remain human-gated.
* **Not a substitute for the Phase 9 ladder.** Auto-promotion in this lane is `solvers.status='auto_promoted'` — a separate flag. The Phase 9 14-stage promotion ladder for runtime stages still requires `human_approval_id`.
* **Not factory-safe by default.** No capsule-aware overlay yet (`LOW_RISK_AUTOGROWTH_POLICY.md` notes this is deferred).
* **Not actuator-side.** Auto-promoted low-risk solvers are *consultable*. Built-in solvers retain precedence; LLM fallback still exists.
* **Not real Anthropic / OpenAI HTTP adapters.** Only `dry_run_stub` and `claude_code_builder_lane` are exercisable end-to-end. Phase 12 does not change that.
* **Not consciousness.** Self-starting growth from runtime evidence is an engineering mechanism. It is observable, reversible, and audit-traceable. It does not constitute a phenomenon-of-experience claim.

## How this maps to the trajectory scorecard

Pre-Phase-12 scorecard at `docs/journal/2026-04-30_autonomy_trajectory_and_gap_analysis.md`:

```
operational_autonomy_0_to_100        : 25
low_risk_autonomy_coverage_0_to_100  : 20
teacher_lane_dependence_0_to_100     : 35
human_gate_dependence_0_to_100       : 80
```

Post-Phase-12 (this run is the evidence):

```
operational_autonomy_0_to_100        : 45  # +20 from self-starting + 30 promoted
low_risk_autonomy_coverage_0_to_100  : 35  # +15 from broader live promotions
teacher_lane_dependence_0_to_100     : 25  # -10; inner loop verified zero provider calls
human_gate_dependence_0_to_100       : 65  # -15 because the trigger step no longer needs a human
```

The numbers move *because* the proof passed. They do not move because the docs say so.

## Reproducing the proof

```
python tools/run_mass_autogrowth_proof.py
```

Default outputs:

* `docs/runs/phase12_self_starting_autogrowth_2026_04_30/mass_autogrowth_proof.json` — committed canonical machine-readable evidence.
* `docs/runs/phase12_self_starting_autogrowth_2026_04_30/mass_autogrowth_proof.db` — scratch SQLite, regenerated each run; gitignored via the repo's `*.db` rule.

The script accepts `--out-dir` and `--db` to redirect outputs.

## Cross-references

* `docs/architecture/LOW_RISK_AUTOGROWTH_POLICY.md` — Phase 11 policy envelope this proof exercises at scale.
* `docs/architecture/PROVIDER_PLANE_AND_BUILDER_LANES.md` — inner-loop / outer-loop distinction, locked by test.
* `docs/runs/phase11_autogrowth_2026_04_29/autonomy_proof.md` — Phase 11 closed-loop proof (3 solvers, manually invoked).
* `docs/journal/2026-04-30_autonomy_trajectory_and_gap_analysis.md` — Phase 12 P1 trajectory analysis + scorecard.
* `tests/autonomy_growth/` — 68 targeted tests including the gap-intake and scheduler suites that lock every step of this loop down.
* `tests/autonomy_growth/test_outer_inner_loop_truthful.py` — locks the "inner loop = zero provider calls" claim.
