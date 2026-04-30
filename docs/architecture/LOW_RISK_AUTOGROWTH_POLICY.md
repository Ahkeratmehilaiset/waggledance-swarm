# Low-Risk Autogrowth Policy

**Status:** new in Phase 11. Defines the bounded envelope inside which WaggleDance may grow new deterministic solvers *without a human in the inner loop*. Composes onto `docs/architecture/SOLVER_BOOTSTRAP_AND_SYNTHESIS.md` (Phase 10 P4) and `docs/architecture/CONTROL_PLANE_AND_DATA_PLANE.md` (Phase 10 P2).

**Hard scope:** this policy applies to deterministic, side-effect-free declarative solvers compiled by `waggledance/core/solver_synthesis/deterministic_solver_compiler.py`. Anything outside that envelope still flows through the existing Phase 9 promotion ladder + human gate.

## What "low-risk" means here

A solver candidate is *low-risk* if **all** the following hold at validation time:

1. **Deterministic.** Same input → same output, byte-identical, on any host.
2. **Side-effect-free.** No file mutation except control-plane / MAGMA / append-only logs. No network access. No subprocess spawn. No actuator write. No policy mutation. No write to runtime configuration.
3. **Pure-function envelope.** Compiled artifact is a closed expression over the inputs and constants. No reflection, no eval, no import-by-string at execution time.
4. **Bounded latency.** Single execution must complete in well under 100 µs at the 99th percentile on a single core. The compiled-artifact executor enforces the envelope by construction (no loops over unbounded data).
5. **Traceable validation lineage.** Every promotion is backed by a `validation_runs` row + a `shadow_evaluations` row + a `promotion_decisions` row in the control plane.
6. **Reversible.** A `rollback` decision against the same solver flips it back to `status='deactivated'` in a single transaction; the decision is recorded.

If any one of these fails, the candidate is **not** auto-promoted. It still flows through the existing Phase 9 promotion ladder and the Phase 9 human gate.

## What "factory-safe" means

In the WaggleDance vocabulary, *factory-safe* is a stricter overlay used by capsules with industrial blast radius (`factory_v1`). For Phase 11 the autogrowth lane is *not* factory-safe by default. It is *capsule-neutral by default* and may be tightened per-capsule later (the `family_policies` table carries the policy per `(family_kind, capsule_id)` pair when needed; v1 of this lane only carries policy per `family_kind`).

A future capsule-aware extension is out of scope here.

## Allowlist — auto-promote-eligible families

The Phase 11 allowlist is the smallest set of Phase 9 declarative families that meet the §1 envelope under their existing deterministic compilers + a runtime executor that is added in this phase. Six families:

| family_kind | required spec keys | runtime semantics | auto-promote eligible |
|---|---|---|---|
| `scalar_unit_conversion` | `from_unit`, `to_unit`, `factor` | `y = x * factor + offset` | yes |
| `lookup_table` | `table` | `table.get(key, default)` | yes |
| `threshold_rule` | `threshold`, `operator`, `true_label`, `false_label` | `x op threshold → label` | yes |
| `interval_bucket_classifier` | `intervals` | first interval `[min, max)` containing `x` | yes |
| `linear_arithmetic` | `coefficients`, `intercept` | `sum(c_i * x_i) + b` over `input_columns` | yes |
| `bounded_interpolation` | `knots`, `method`, `min_x`, `max_x` | piecewise linear over knots, clip / null at edges | yes |

These six are the universe of `LOW_RISK_FAMILY_KINDS` in `waggledance/core/autonomy_growth/low_risk_policy.py`. The code constant and this doc must agree; a regression test in `tests/autonomy_growth/test_low_risk_policy.py` enforces parity.

## Excluded from the auto-promote allowlist (still human-gated)

| family_kind | reason |
|---|---|
| `weighted_aggregation` | side-effect-free, but the operator-prioritised target was the six above; revisit when the v1 lane is observed in production |
| `temporal_window_rule` | requires time-series state; not a pure function of single input; would need a windowed-store contract before it can be auto-promoted safely |
| `structured_field_extractor` | regex / json-path on arbitrary input fields; extraction semantics are ambiguous and easy to silently drift; conservative deferral |
| `deterministic_composition_wrapper` | references other solver_specs by id; brittle until composition-resolution store is hardened |

These remain accessible through the existing Phase 9 candidate / quarantine / promotion-ladder pipeline. They *can* be promoted, but only by a reviewer using the human gate. Phase 11 does not change that path.

## Invariants required before auto-promotion

The auto-promotion engine in `waggledance/core/autonomy_growth/auto_promotion_engine.py` MUST verify, in order:

| # | Invariant | Source of truth |
|---|---|---|
| I1 | family_kind ∈ allowlist | `low_risk_policy.LOW_RISK_FAMILY_KINDS` |
| I2 | family policy exists in `control_plane.family_policies` and `is_low_risk = 1` | `family_policies` row |
| I3 | candidate spec validates per Phase 9 `validators.SyntacticValidator` | Phase 9 unchanged |
| I4 | candidate compiles deterministically twice with same hash | `deterministic_solver_compiler.compile_spec` run twice; canonical_json equal |
| I5 | validation pass rate ≥ `family_policies.min_validation_pass_rate` (default `1.0`) | `validation_runs` row this run |
| I6 | shadow evaluation sample count ≥ `family_policies.min_shadow_samples` (default `5`) | `shadow_evaluations` row this run |
| I7 | shadow agreement rate ≥ `family_policies.min_shadow_agreement_rate` (default `1.0`) | `shadow_evaluations` row this run |
| I8 | no existing solver with same `(family_kind, solver_name)` already in `status='auto_promoted'` | `solvers` table |
| I9 | promotion budget for the family not exceeded — `count(solvers WHERE family_kind=X AND status='auto_promoted') < family_policies.max_auto_promote` (default `100`) | `solvers` + `family_policies` |
| I10 | candidate spec carries no absolute paths or secrets | `validators.NoLeakValidator` |

If any invariant fails, the candidate is **rejected** and a `promotion_decisions` row is recorded with `decision='rejected'` and the failing invariant id. No partial activation is permitted; the decision write and the solver `status` flip happen in the same SQLite transaction.

## Auto-promotion semantics

* `decided_by = 'autopromotion_engine_v1'`. Never a human id. The Phase 9 human-gate `human_approval_id` field is **not** populated by this lane and the existing Phase 9 promotion-ladder transitions for runtime stages (post-campaign-runtime-candidate / canary-cell / limited-runtime / full-runtime) still require a real `human_approval_id`. Auto-promotion in this lane is a *separate status flag* (`solvers.status = 'auto_promoted'`), not a runtime-stage promotion.
* The runtime dispatcher (§ "Runtime executability" below) treats `status='auto_promoted'` solvers as **consultable in a bounded safe lane between built-in solvers and LLM fallback**. They are not given precedence over built-in authoritative solvers. They do not bypass the Reality View 11-panel never-fabricate invariant.
* The dispatcher records every cache-miss / cache-hit against an auto-promoted solver in `autonomy_kpis`. KPIs are aggregate; the runtime never lists per-solver state at scale.

## Rollback / deactivation

A rollback may be triggered by:

* a downstream consumer reporting a regression — recorded as a `promotion_decisions` row with `decision='rollback'` and `rollback_reason`;
* an operator command (`tools/wd_autonomy_deactivate.py --solver-name X`) — same row shape;
* an automatic deactivation if shadow agreement drops below the policy minimum on subsequent shadow runs.

A rollback is a single transaction:

* `solvers.status` transitions `auto_promoted` → `deactivated`,
* a `promotion_decisions` row records the rollback,
* the dispatcher will not consider a `deactivated` solver until a fresh promotion decision flips it back.

There is no force-delete. The historical `promotion_decisions` rows are append-only.

## What this policy explicitly does NOT do

* Does NOT promote solvers across the 14-stage Phase 9 promotion ladder. Runtime stages still require `human_approval_id`. The Phase 9 human gate is preserved untouched.
* Does NOT execute the Stage-2 atomic flip. `core/faiss_store.py:26 _DEFAULT_FAISS_DIR=data/faiss/` is unchanged. The Stage-2 cutover RFC at `docs/architecture/STAGE2_CUTOVER_RFC.md` still gates that mechanism.
* Does NOT introduce a new HUMAN_APPROVAL artifact. RULE 4: no approval collected in this session.
* Does NOT touch `phase8.5/*` branches. They remain READ_ONLY.
* Does NOT write to actuators, policies, or production runtime configuration. Auto-promoted low-risk solvers are *consultable*, not *authoritative*.
* Does NOT replace built-in authoritative solvers. The dispatcher precedence is built-in → auto-promoted-low-risk → LLM fallback.
* Does NOT change the never-fabricate invariant of Reality View. Missing data still surfaces as `available=false` with a structured rationale.

## Cross-references

* `waggledance/core/autonomy_growth/low_risk_policy.py` — code constant for `LOW_RISK_FAMILY_KINDS`; parity with this doc enforced by test.
* `waggledance/core/autonomy_growth/auto_promotion_engine.py` — implements the I1–I10 invariants.
* `waggledance/core/autonomy_growth/solver_dispatcher.py` — runtime-executable lane.
* `waggledance/core/autonomy_growth/solver_executor.py` — per-family pure executors.
* `waggledance/core/storage/control_plane_schema.py` — schema v2 with the five new tables.
* `docs/architecture/SOLVER_BOOTSTRAP_AND_SYNTHESIS.md` — Phase 10 candidate-creation pipeline that feeds this lane.
* `docs/architecture/CONTROL_PLANE_AND_DATA_PLANE.md` — substrate this lane reads from / writes to.
* `docs/architecture/STAGE2_CUTOVER_RFC.md` — explicitly out of scope for this policy.
* `CLAUDE.md` — operator rules; §10 atomic-flip discipline is preserved.
