# Phase 17A — Implementation Plan (P2)

**Date:** 2026-05-04
**Status:** authored before any code is written. Operator may review at this point before P3 begins (the master prompt explicitly places a 60–90 min planning gate here).

## Goal recap

Close the producer-fabric gap on main and prove 10k synthetic solver descriptor capability lookup, gated by fail-closed release decision and zero v3.8.0 disturbance.

## Architectural decisions

1. **Source branch for port:** `origin/phase8.5/hive-proposes` (most-integrated state).
2. **Port style:** core modules verbatim (pure-stdlib + waggledance siblings); skip phase8.5 CLI monoliths; write a single Phase 17A orchestrator.
3. **Test style:** skip phase8.5 fixture-heavy tests; write Phase 17A integration tests against IR adapter end-to-end.
4. **10k scale proof:** generate 10,000 synthetic deterministic descriptors balanced across 6 families × 8 hex cells; exercise real `RuntimeQueryRouter` capability lookup path; record build/index time + lookup p50/p95/p99.
5. **Seed corpus growth:** target 104 → ≥128 (+24) within existing 6 families; stretch ≥160 if proof stability holds.
6. **No allowlist widening, no high-risk autopromote, no Stage-2 flip, no HUMAN_APPROVAL, no actuator writes.**
7. **Provider/builder delta = 0** in every proof.
8. **Docker `--network none`** verifies producer fabric + 10k scale + restart continuity.
9. **Release decision:** fail-closed. v3.9.0-producer-fabric-alpha (PRERELEASE only) if every gate passes; NO TAG otherwise.

## Phased execution plan

### P3 — Producer fabric integration

**P3.1 Cherry-pick 14 producer module files** from `origin/phase8.5/hive-proposes` into `phase17a/producer-fabric-scale`:
* `waggledance/core/dreaming/__init__.py`
* `waggledance/core/dreaming/curriculum.py`
* `waggledance/core/dreaming/collapse.py`
* `waggledance/core/dreaming/meta_proposal.py`
* `waggledance/core/dreaming/replay.py`
* `waggledance/core/dreaming/request_pack.py`
* `waggledance/core/dreaming/shadow_graph.py`
* `waggledance/core/magma/self_model.py`
* `waggledance/core/magma/reflective_workspace.py`
* `waggledance/core/meta/__init__.py`
* `waggledance/core/meta/history.py`
* `waggledance/core/meta/inputs.py`
* `waggledance/core/meta/meta_learner.py`
* `waggledance/core/meta/review_bundle.py`

Verify each file:
- has `# SPDX-License-Identifier: BUSL-1.1` (add if missing)
- compiles via `python -m compileall <file>`
- imports resolve on main

If any file fails to compile or import, demote it to WRAPPER and write a thin facade.

**P3.2 Author Phase 17A orchestrator** `tools/run_phase17a_producer_fabric_proof.py` (~400-500 LOC):

Pipeline:
```
fixture corpus
  → gap-mining (small inline helper using ported algorithms)
  → curiosity_log JSON
  → self-model snapshot via waggledance.core.magma.self_model
  → workspace_tensions + blind_spots JSON
  → dream curriculum via waggledance.core.dreaming.curriculum
  → nights + target_items JSON
  → meta-learner via waggledance.core.meta.meta_learner
  → hive proposals JSON
  → review bundle via waggledance.core.meta.review_bundle
  → review_bundle JSON
  → IR adapter ingestion via waggledance.core.ir.adapters.from_*
  → IR bundle (canonical pre-compiler format)
  → final proof artifact JSON
```

Constraints:
- argparse: `--out-dir`, `--db` (optional)
- offline only: no network, no provider calls
- deterministic: fixture corpus → identical proof artifact across runs
- no HUMAN_APPROVAL: rejected with `kind="rejected_human_approval_in_offline_proof"`
- no Stage-2 flip request: rejected with `kind="rejected_stage2_flip_in_offline_proof"`
- 6 negative cases: missing input, malformed input, high-risk proposal, manual hint injection, Stage-2 request, unknown family

Output JSON shape (top-level keys):
- `phase`: "phase17a_producer_fabric"
- `corpus_total`: int (fixture cases)
- `producers_run`: ["curiosity", "self_model", "dream", "hive"]
- `ir_objects_emitted_total`: int
- `ir_objects_per_kind`: dict
- `negative_cases_passed`: 6 / 6
- `provider_jobs_delta_during_proof`: 0
- `builder_jobs_delta_during_proof`: 0
- `produced_artifacts`: list of file paths

**P3.3 Author Phase 17A integration tests** `tests/autonomy_growth/test_phase17a_producer_fabric_proof.py` (~300-500 LOC):

Test cases:
- `test_proof_emits_corpus_total_>=_fixture_count`
- `test_proof_emits_all_4_producers`
- `test_proof_ir_objects_consumed_by_existing_adapters`
- `test_proof_negative_case_missing_input_rejected`
- `test_proof_negative_case_malformed_artifact_rejected`
- `test_proof_negative_case_high_risk_proposal_rejected`
- `test_proof_negative_case_manual_human_approval_in_offline_proof_rejected`
- `test_proof_negative_case_stage2_flip_request_rejected`
- `test_proof_negative_case_unknown_family_rejected`
- `test_proof_provider_jobs_delta_zero`
- `test_proof_builder_jobs_delta_zero`
- `test_proof_deterministic_across_runs` (run twice, assert identical artifact JSON modulo timestamps)
- `test_phase17a_producers_no_actuator_writes`

### P4 — 10k synthetic descriptor scale proof

**P4.1 Author** `tools/run_solver_scale_proof.py` (~300-400 LOC):

Generate descriptors:
- 10,000 synthetic deterministic capability descriptors
- balanced across 6 families × 8 hex cells (~208 per cell-family combination)
- each descriptor has unique `solver_name`, `family_kind`, `cell_id`, `feature_set` (matching `family_features.py` per-family schema)
- deterministic IDs (sha256 of feature set seed)

Exercise capability lookup:
- bulk-load descriptors into `solver_capability_features` table via existing `ControlPlaneDB` API
- run `RuntimeQueryRouter.route()` over a sample of structured queries (~1000 queries balanced across families)
- assert capability lookup path is hit (not fallback to FIFO)
- record p50/p95/p99 lookup latency

Constraints:
- offline / no network
- no provider calls (`provider_jobs_delta = 0`)
- no builder calls (`builder_jobs_delta = 0`)
- LABEL output as **synthetic-scale, NOT canonical proof corpus** (master prompt rule)

Output JSON:
- `synthetic_solver_descriptors_total`: 10000
- `descriptors_per_family`: dict (~1667 each)
- `descriptors_per_hex_cell`: dict (~1250 each)
- `families_total`: 6
- `hex_cells_total`: 8
- `index_build_time_seconds`: float
- `lookup_pass_count`: 1000
- `lookup_p50_ms`: float
- `lookup_p95_ms`: float
- `lookup_p99_ms`: float
- `lookup_capability_hits_total`: int (must equal lookup_pass_count if real path exercised)
- `lookup_fifo_fallback_total`: int (must be 0 for honest claim)
- `provider_jobs_delta`: 0
- `builder_jobs_delta`: 0
- `is_synthetic_scale`: true
- `not_canonical_corpus`: true

**P4.2 Author** `tests/autonomy_growth/test_solver_scale_proof.py` (~150-300 LOC):

- `test_scale_proof_emits_at_least_10000_descriptors`
- `test_scale_proof_balanced_across_6_families`
- `test_scale_proof_balanced_across_8_hex_cells`
- `test_scale_proof_capability_lookup_hits_all`
- `test_scale_proof_no_fifo_fallback`
- `test_scale_proof_provider_delta_zero`
- `test_scale_proof_clearly_labeled_synthetic`

### P5 — Canonical seed corpus growth (optional, stretch)

**Target:** 104 → 128 (+24) within existing 6 families. Stretch: 160. Within budget: skip if P3+P4 already substantial.

If attempted:
- per-family additions: +4 each (28→32 scalar, 17→21 lookup/threshold, 14→18 interval/linear/interp) OR
- weighted: +6 scalar, +5 lookup, +5 threshold, +3 interval, +3 linear, +2 interp = +24
- each new seed has deterministic spec + validation_cases + shadow_samples
- re-run Phase 15/16A/16B proofs to assert stability
- if proofs flake on new seeds, revert and document

Test:
- `test_seed_library_meets_phase17a_material_growth_minimum` (≥128 if attempted, ≥104 always)

### P6 — Competitive evidence docs

**Files:**
- `docs/benchmarks/COMPETITIVE_EVIDENCE_MATRIX_2026.md`
- `docs/benchmarks/LOCAL_AI_RUNTIME_COMPARISON.md`

Per axis: claim | evidence artifact | label (PROVEN / MEASURED / INFERRED / NOT CLAIMED) | strengthening path.

Axes per master prompt P6: deterministic routing, audit/replay, zero-provider, Docker --network none, restart, producer fabric, 10k scale, corpus size, raw intelligence (NOT CLAIMED), MoE fallback, edge, autonomous learning, safety gate.

Forbidden language (master prompt rule 15): conscious, sentient, aware, alive, AGI, revolutionary, magical, human-like mind, self-aware, explosive intelligence, emergent, "beats all competitors".

### P7+P8 — Targeted tests + Docker verification

**Targeted tests:**
- `pytest tests/autonomy_growth/ -q`
- `pytest tests/phase10/ -q`
- `pytest tests/storage/ tests/ui_hologram/ tests/autonomy/test_solver_router.py -q`

**Docker:**
- `docker build -t waggledance:phase17a .`
- `docker run --rm --network none waggledance:phase17a python tools/run_phase17a_producer_fabric_proof.py --out-dir /tmp/p17a`
- `docker run --rm --network none waggledance:phase17a python tools/run_solver_scale_proof.py --out-dir /tmp/p17a --descriptors 10000`
- `docker run --rm --network none waggledance:phase17a python tools/run_full_restart_continuity_proof.py --out-dir /tmp/p17a --db /tmp/p17a/restart.db`

If `.dockerignore` needs minimal carve-out for the new tools, apply it (already established pattern from Phase 16F).

### P9 — Release decision

**Option A (v3.9.0-producer-fabric-alpha PRERELEASE)** allowed only if all gates green.
**Option B (NO TAG)** if any required gate fails.
**v3.8.0 must NOT change.**

### P10–P12 — Commit, PR, post-merge, tag

Commit groups:
1. `chore(phase85): preserve producer branches inventory` (Phase 17A P1 docs)
2. `feat(producers): port phase8.5 producer modules to main` (the 14-file cherry-pick)
3. `feat(phase17a): producer fabric proof orchestrator + integration tests`
4. `feat(scale): 10k solver capability scale proof + tests`
5. `feat(seeds): expand low-risk canonical seed corpus by +24 entries` (if attempted)
6. `docs(phase17a): evidence matrix and release readiness`
7. `chore(license): add SPDX BUSL headers to ported producer modules` (if needed)

PR title: `Phase 17A — producer fabric and 10k solver scale proof`

Autonomous squash-merge with `--match-head-commit` if guardrails pass.

Post-merge: `git checkout --detach origin/main` + rerun all Phase 17A proofs locally + Docker rebuild as `waggledance:v3.9.0-producer-fabric-alpha-rc` + post-merge fresh clone proof.

### P13 — Tag

If decision A:
- `git tag -a v3.9.0-producer-fabric-alpha -m "v3.9.0-producer-fabric-alpha — Phase 17A producer fabric + 10k solver scale"`
- `git push origin v3.9.0-producer-fabric-alpha`
- `gh release create v3.9.0-producer-fabric-alpha --prerelease ...`
- Verify `isPrerelease=true`, v3.8.0 still Latest.

### P14 — Final report

All required bullets per master prompt P14.

## Wall-clock estimate

| phase | estimate | confidence |
|---|---|---|
| P0+P1 (already done) | done | 100% |
| P2 (this doc + reconciliation matrix + producer gap audit) | done in ~30 min | 100% |
| P3 producer fabric port + orchestrator + tests | 2-3 hours | medium |
| P4 10k scale proof + tests | 1.5-2 hours | medium |
| P5 seed corpus growth (optional) | 1-1.5 hours if attempted | low (may skip for budget) |
| P6 competitive evidence docs | 30-45 min | high |
| P7+P8 targeted tests + Docker rebuild + verification | 1-1.5 hours | high |
| P9 release decision doc | 10 min | high |
| P10 commit + push + branch-ref clone + PR | 30 min | high |
| P11 wait for CI + autonomous merge | 10-15 min | high (CI is ~6 min on similar work) |
| P11.5 post-merge proofs + Docker rebuild + fresh clone | 1-1.5 hours | high |
| P12 tag + GitHub release | 15 min | high |
| P13 post-release docs PR (if needed) | 30 min | medium |
| P14 final report | 15 min | high |

**Sum: 7.5–11 hours.** At the upper edge of the 10h budget. May skip P5 (seed growth) to stay within budget; the master prompt explicitly allows this with a documented rationale.

## Key risks tracked

1. `dreaming/collapse.py` lazy-loads `propose_solver.py` — if absent or refactored, wrap in try/except.
2. SPDX header gaps in phase8.5 producer files — add at port time.
3. Test-collection conflicts if any phase8.5 test name collides with main test name (need to verify after port).
4. Docker rebuild duration (~7 min on Phase 16F baseline) — predictable.
5. CI duration (~6 min PR-level on Phase 16F baseline) — predictable.
6. Post-merge Docker rebuild may take longer if cache invalidates due to new files.
7. PR review surface: ~5,500-6,500 LOC diff is large; logical commit groups + clear PR body required.

## Operator review point

Per master prompt rule 17 ("First 60–90 minutes must be planning and inventory before feature coding"), this is the natural moment for the operator to either:
* approve and let P3 begin; OR
* course-correct (e.g. demand a smaller scope, or insist on a phase8.5 test port, or ask to skip seed growth).

The session continues autonomously after this artifact lands unless the operator interrupts.

## Stop / handoff triggers active for P3+

Per master prompt:
* Producer code requires unsafe runtime activation → stop, no tag.
* Producer proof fails → stop, no tag.
* 10k scale proof fails → stop, no tag.
* Provider/builder delta != 0 → stop, no tag.
* Docker fails → stop, no tag.
* Fresh clone fails → stop, no tag.
* CI fails for non-shallow-clone reason → stop, no tag.
* Wall clock 10h exceeded → stop, write no-tag handoff.
* Rule violation required → stop, no tag.
