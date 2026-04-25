# Hooks: dream pipeline → meta-learner

`contract_version: 1`

This contract describes how a future meta-learner / `hive_proposes`
session may consume this session's `dream_meta_proposal` artifacts.
It is consumer-only: nothing in this contract authorizes runtime
mutation, axiom write, or live registration during Session C.

## Meta-proposal format

Top-level keys (canonical JSON, sorted):

- `schema_version`
- `continuity_anchor`
- `consumed_hook_contracts`
- `source_tension_ids`
- `selected_proposal` — `{path, proposal_id, solver_name, cell_id, solver_hash}`
- `gate_provenance`
- `collapse_results` — `{collapse_verdict, raw_verdict}`
- `replay_metrics` — `{replay_methodology, replay_case_count, targeted_case_count, structural_gain_count, protected_case_regression_count, unresolved_case_count, estimated_fallback_delta}`
- `structural_gains` — `{structural_gain_ratio, min_gain_ratio_required}`
- `expected_value_of_merging`
- `confidence`
- `tension_severity_max`
- `uncertainty` — one of `low | medium | high`
- `why_human_review_required`
- `why_runtime_flip_is_out_of_scope`
- `structurally_promising`

## Gate provenance interpretation

For each gate name in `gate_provenance`:

- `native` — full Phase 8 gate; treat as authoritative.
- `shadow_proxy` — deterministic proxy; treat as advisory and
  re-run the native gate before any merge.
- `unavailable` — gate did not run; do not assume a pass.

Aggregate `gate_provenance_summary` (in the collapse report) carries
the worst-case across all evaluated proposals.

## Structural gain interpretation

`structural_gain_count` reflects matches between a case's
`suspected_missing_capability` / `bridge_candidate_refs` and the new
nodes/edges in the shadow graph diff. It is a **structural proxy**,
not validated correctness.

`estimated_fallback_delta = structural_gain_count / replay_case_count`.

`structurally_promising = true` requires:

- `collapse_verdict == ACCEPT_CANDIDATE`
- `structural_gain_count > 0`
- `protected_case_regression_count == 0`
- `structural_gain_count / max(targeted_case_count, 1) >= 0.10`

## why_human_review_required semantics

Human review must validate:

1. The selected proposal's solver tests actually pass on real data.
2. No protected case regresses under live execution (not just
   structural).
3. The proposal's `llm_dependency.required` declaration is honest.
4. The cell is the correct destination for the new solver.
5. No hidden side channels exist (re-scan with the no-secrets-no-paths
   gate set to its strictest mode).

The meta-learner MUST NOT bypass any of these by acting on a
structurally promising flag alone.

## Forbidden actions for the meta-learner consumer

- promoting an `ACCEPT_CANDIDATE` proposal to live runtime
- writing axiom YAML
- writing FAISS indexes
- mutating any file under `waggledance/core/dreaming/*` without
  updating the BUSL Change Date in the same commit (currently
  `2030-03-19`)
- skipping human review when `confidence < 1.0` (which is always
  true for shadow-only meta-proposals by construction)
