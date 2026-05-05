# Phase 18C — Mined Solver Runtime Dispatch Integration (Design)

**Status:** Authoritative for Phase 18C. Code in `waggledance/core/autonomy_growth/mined_solver_runtime.py`, the proof at `tools/run_phase18c_mined_solver_runtime_dispatch_proof.py`, and tests at `tests/autonomy_growth/test_phase18c_mined_solver_runtime_dispatch.py` MUST conform to this document.

This document exists per master-prompt rule "Do not code until this exists."

---

## 1. Purpose

Close the explicit Phase 18B gap (`capability_lookup_status = NOT_RUN_OUT_OF_PHASE18B_SCOPE`) by registering Phase 18B mined low-risk solver specs into the real `ControlPlaneDB` and dispatching them through the real `LowRiskSolverDispatcher.dispatch_by_features()` path. No fake standalone dispatch.

## 2. Real runtime path inventory (verified before coding)

The repo already has the full runtime dispatch chain. Phase 18C reuses it verbatim — no new dispatcher, no new executor, no new promotion engine.

| Component | File:Lines | What it is |
| --- | --- | --- |
| `RuntimeQueryRouter.route(RuntimeQuery)` | `waggledance/core/autonomy_growth/runtime_query_router.py:152` | Router entry point. Three-tier precedence: built-in → auto-promoted → fallback. |
| `LowRiskSolverDispatcher.dispatch_by_features()` | `waggledance/core/autonomy_growth/solver_dispatcher.py:82` | Capability-aware lookup. Calls `ControlPlaneDB.find_auto_promoted_solvers_by_features(family_kind, features, limit=1)`. |
| `ControlPlaneDB.find_auto_promoted_solvers_by_features` | `waggledance/core/storage/control_plane.py:1439` | SQL query over `solver_capability_features` table with feature superset match + `status='auto_promoted'`. |
| `execute_artifact(artifact, inputs)` | `waggledance/core/autonomy_growth/solver_executor.py:220` | Pure dispatcher to per-family executors via `_EXECUTORS[artifact["kind"]]`. Raises `UnsupportedFamilyError` for non-allowlisted kinds. |
| Six per-family executors | `solver_executor.py:46-201` | `_exec_scalar_unit_conversion`, `_exec_lookup_table`, `_exec_threshold_rule`, `_exec_interval_bucket`, `_exec_linear_arithmetic`, `_exec_bounded_interpolation`. All pure functions. |
| Canonical registration pattern | `tools/run_solver_scale_proof.py:225-266` | The Phase 17A 10k-scale proof's `bulk_load_descriptors()` shows the four-step ControlPlaneDB sequence: `upsert_solver_family` → `upsert_solver(status='auto_promoted')` → `set_solver_capability_features` → `upsert_solver_artifact`. |

## 3. Phase 18B inventory

| Component | File:Lines |
| --- | --- |
| `GapVerdict` enum (six values) | `waggledance/core/autonomy_growth/gap_candidate.py` |
| `GapCandidate`, `GapMiningResult` | `waggledance/core/autonomy_growth/gap_candidate.py` |
| `mine_runtime_gaps(signals, *, config)` | `waggledance/core/autonomy_growth/gap_mining.py` |
| `candidate_to_solver_spec(candidate)` | `waggledance/core/autonomy_growth/gap_mining.py` |
| `build_quarantined_builder_handoff(candidate)` | `waggledance/core/autonomy_growth/gap_mining.py` |
| 30-signal synthetic fixture | `tools/run_phase18b_gap_miner_feedback_proof.py::build_synthetic_fixture` |
| Six-family allowlist | `gap_mining.py::ALLOWED_FAMILIES` |

The Phase 18B fixture deterministically yields exactly 6 ALLOWLISTED candidates (one per family).

## 4. Integration decision

**Reuse the real runtime path. No fake standalone dispatch.**

Phase 18C's mainline module `waggledance/core/autonomy_growth/mined_solver_runtime.py`:

* Calls `mine_runtime_gaps()` then `candidate_to_solver_spec()` on each ALLOWLISTED candidate.
* Compiles each mined spec into an executor-shaped artifact via a small `_compile_runtime_artifact_for_family(spec)` per-family mapper. The mapper handles exactly the six fixture shapes Phase 18B emits; an unrecognized `(family_kind, feature_dict)` signature fails closed with `RuntimeArtifactCompilationError`.
* Registers each compiled artifact via the canonical four-step `ControlPlaneDB` sequence (same pattern as `tools/run_solver_scale_proof.py:225-266`).
* Dispatches through `LowRiskSolverDispatcher.dispatch_by_features()` — the same method the live runtime uses.

**Why a small per-family compiler is OK:** mined specs from `candidate_to_solver_spec()` are *descriptive* (they record `feature_dict`, `evidence_refs`, `training_examples` hints) — not yet executable. An operator reviewing a mined spec normally translates it into the executor schema. Phase 18C automates that translation only for the six known mined-fixture shapes; arbitrary new shapes still require human review and fail closed.

**No autonomy-extension creep:**

* No new dispatcher, executor, router, or promotion engine.
* No allowlist widening.
* The compiler is not a model; it's a fixed lookup table.
* Builder handoff and the other four non-allowlisted verdicts are explicitly rejected from registration.

## 5. Public API (mainline module)

```python
# waggledance/core/autonomy_growth/mined_solver_runtime.py

class RuntimeArtifactCompilationError(Exception): ...

def compile_mined_spec_to_runtime_artifact(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Compile a Phase 18B mined spec into an executor-shaped artifact.

    Raises RuntimeArtifactCompilationError if (family_kind, canonical
    feature_dict) is not in the documented compilation table.
    """

def register_mined_solver_specs(
    *,
    candidates: Sequence[GapCandidate],
    control_plane: ControlPlaneDB,
) -> RegistrationSummary:
    """Register ALLOWLISTED candidates into ControlPlaneDB. Refuse all
    other verdicts. Idempotent (same candidate_id is registered once)."""

@dataclass(frozen=True)
class RegistrationSummary:
    registered_solver_ids: tuple[int, ...]
    registered_candidate_ids: tuple[str, ...]
    rejected_count: int
    rejected_by_verdict: Mapping[str, int]
    builder_handoff_quarantined: int
    duplicates_suppressed_in_run: int
```

`register_mined_solver_specs` is fail-closed:

1. For each `GapCandidate`:
   1. If `verdict != ALLOWLISTED_SOLVER_SPEC`: count under `rejected_by_verdict[verdict]`, do not register.
   2. If `family_kind not in ALLOWED_FAMILIES`: defense-in-depth reject (should never happen for ALLOWLISTED).
   3. Compile the artifact (`compile_mined_spec_to_runtime_artifact`). If it raises, reject and count under `rejected_by_verdict["COMPILATION_FAILED"]`.
   4. If `candidate_id` already registered in this run: skip (idempotent).
   5. Else execute the four-step ControlPlaneDB sequence. Capture the assigned solver row id.
2. Return `RegistrationSummary`.

## 6. Compilation table (six fixture shapes)

| family_kind | mined `feature_dict` (Phase 18B fixture) | compiled artifact |
| --- | --- | --- |
| `scalar_unit_conversion` | `{"input_unit":"km","output_unit":"miles","rule":"1 km = 0.621371 miles"}` | `{"kind":"scalar_unit_conversion","factor":0.621371,"offset":0.0}` |
| `lookup_table` | `{"table_name":"chemical_symbols","example_key":"tin"}` | `{"kind":"lookup_table","table":{"tin":"Sn","gold":"Au","sodium":"Na","iron":"Fe"},"default":"unknown"}` |
| `threshold_rule` | `{"threshold":30,"example_value":37,"rule":"above_or_below"}` | `{"kind":"threshold_rule","operator":">","threshold":30,"true_label":"above","false_label":"below"}` |
| `interval_bucket_classifier` | `{"buckets":"[0,10),[10,20),[20,30)","example_value":17}` | `{"kind":"interval_bucket_classifier","intervals":[{"min":0,"max":10,"label":"[0,10)"},{"min":10,"max":20,"label":"[10,20)"},{"min":20,"max":30,"label":"[20,30)"}],"out_of_range_label":"out_of_range"}` |
| `linear_arithmetic` | `{"operator":"add","example_inputs":{"a":14,"b":9}}` | `{"kind":"linear_arithmetic","input_columns":["a","b"],"coefficients":[1.0,1.0],"intercept":0.0}` |
| `bounded_interpolation` | `{"endpoints":"(0,0)->(10,100)","example_x":3}` | `{"kind":"bounded_interpolation","min_x":0.0,"max_x":10.0,"knots":[{"x":0.0,"y":0.0},{"x":10.0,"y":100.0}],"method":"linear","out_of_range_policy":"clip"}` |

The compilation table is keyed by `(family_kind, sha256(canonical_json(feature_dict))[:16])`. If a future mined spec has the same family but a different feature_dict, it fails closed — the operator must add a new compilation rule (and review).

## 7. Dispatch proof contract

The Phase 18C proof harness:

1. Calls `mine_runtime_gaps()` on the Phase 18B 30-signal fixture. Verifies the canonical 14-candidate verdict distribution (6/3/2/1/1/1).
2. Creates a per-test isolated `ControlPlaneDB` (in-memory or `tmp_path` SQLite — the existing test pattern).
3. Calls `register_mined_solver_specs(candidates=result.candidates, control_plane=cp)`. Verifies:
   * `len(registered_solver_ids) == 6`
   * `rejected_count == 8` (all non-ALLOWLISTED verdicts)
   * `rejected_by_verdict` matches `{INSUFFICIENT_EVIDENCE:3, OUT_OF_FAMILY_REJECTED:2, HIGH_RISK_REJECTED:1, BUILDER_HANDOFF_QUARANTINED:1, DUPLICATE_SUPPRESSED:1}`.
4. Constructs a deterministic dispatch fixture: ≥3 cases per family × 6 families = ≥18 cases. Each case is a `(family_kind, features, inputs, expected_output)` tuple.
5. For each case: invokes `LowRiskSolverDispatcher.dispatch_by_features(family_kind, features, inputs)`. Asserts:
   * `result.served is True` (the registered mined solver was used).
   * `result.source == "auto_promoted_solver"`.
   * `result.output == expected_output`.
6. Records all counters + per-case results.

## 8. Per-family dispatch test inputs (≥3 each)

| family | case 1 (input → expected) | case 2 | case 3 |
| --- | --- | --- | --- |
| scalar_unit_conversion | `x=10 → 6.21371` | `x=0 → 0.0` | `x=100 → 62.1371` |
| lookup_table | `key="tin" → "Sn"` | `key="gold" → "Au"` | `key="iron" → "Fe"` |
| threshold_rule | `x=37 → "above"` | `x=12 → "below"` | `x=30 → "below"` |
| interval_bucket_classifier | `x=5 → "[0,10)"` | `x=17 → "[10,20)"` | `x=22 → "[20,30)"` |
| linear_arithmetic | `a=14,b=9 → 23.0` | `a=0,b=0 → 0.0` | `a=5,b=7 → 12.0` |
| bounded_interpolation | `x=3 → 30.0` | `x=0 → 0.0` | `x=10 → 100.0` |

Total = 18 dispatch cases. Every case asserts `served=true, source="auto_promoted_solver"`.

## 9. Output JSON top-level fields

```json
{
  "phase": "phase18c",
  "benchmark_version": "phase18c.v1",
  "base_main_sha": "...",
  "source_prerelease": "v3.10.1-gap-miner-feedback-alpha",
  "candidate_prerelease": "v3.10.2-mined-solver-dispatch-alpha",
  "signals_total": 30,
  "candidates_total": 14,
  "allowlisted_candidate_count": 6,
  "registered_solver_count": 6,
  "rejected_registration_count": 8,
  "rejected_by_verdict": { ... },
  "builder_handoff_quarantine_count": 1,
  "duplicate_suppression_count": 1,
  "dispatch_case_count": 18,
  "dispatch_success_count": 18,
  "dispatch_failure_count": 0,
  "families_covered": 6,
  "per_family_dispatch_counts": { ... },
  "per_dispatch_case": [ ... ],
  "allowlist_unchanged": true,
  "provider_jobs_delta": 0,
  "builder_jobs_delta": 0,
  "no_model_pull_or_download": true,
  "no_cloud_api_calls": true,
  "no_live_builder_execution": true,
  "no_stage2_flip": true,
  "no_human_approval": true,
  "no_high_risk_autonomy": true,
  "no_cross_vendor_ranking_claim": true,
  "no_raw_intelligence_superiority_claim": true,
  "forbidden_claims_absent": true,
  "claim_labels": {
    "runtime_gap_feedback": "PROVEN-WITH-RUNTIME-DISPATCH",
    "mined_solver_specs": "MEASURED-RUNTIME-DISPATCH-MINED-SOLVERS-SIX-FAMILY",
    "builder_handoff": "QUARANTINED-NOT-AUTOPROMOTED",
    "high_risk_families": "NOT_CLAIMED",
    "raw_intelligence_vs_frontier_moe": "NOT_CLAIMED",
    "cross_vendor_ranking": "NOT_CLAIMED",
    "consciousness": "NOT_CLAIMED"
  },
  "release_gate_pass": true
}
```

## 10. Stop conditions (release decision B)

* Real `LowRiskSolverDispatcher.dispatch_by_features()` cannot be reached.
* Any dispatch case returns `served != True` or `source != "auto_promoted_solver"`.
* Any non-ALLOWLISTED candidate registers as an executable solver.
* Builder handoff becomes executable.
* `provider_jobs_delta != 0` or `builder_jobs_delta != 0`.
* Allowlist changes.
* Phase 18A bundle validation fails.
* Phase 18B proof fails.
* Forbidden claims appear.
* Compilation table needs to be widened beyond the six fixture shapes (an unmined feature_dict tries to compile).
* Token/secret exposure.

## 11. Sign-off

This design is the canonical contract for Phase 18C. Any deviation in the implementation must be reflected back into this document in the same PR.
