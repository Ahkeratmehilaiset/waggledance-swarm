# Phase 17A — Phase 8.5 Reconciliation Matrix (P2)

**Date:** 2026-05-04
**Method:** for each file added on `origin/phase8.5/hive-proposes` (the most-integrated phase8.5 branch — contains code from all four producer organs), classify into PORT / WRAPPER / LEAVE per master prompt rule 7.

## Source branches

`origin/phase8.5/hive-proposes` (HEAD `de8c341`) is the most integrated phase8.5 state — it carries forward Session A (curiosity), Session B (self-model), Session C (dream-curriculum), and Session D (hive-proposes) producer code in one branch. It is the natural source-of-truth for the port.

The other 4 phase8.5 branches contain subsets and are preserved on origin per Phase 17A P1 for historical audit only.

## Classification rules

* **PORT** — file is pure-stdlib (or stdlib + waggledance siblings), output JSON matches an existing main IR adapter contract, no Phase 8 obsolete dependencies. Cherry-picked into Phase 17A branch verbatim with SPDX header added.
* **WRAPPER** — file logic is good but it imports an obsolete or refactored Phase 8 module. Build a thin facade that uses Phase 16G API.
* **LEAVE** — file is phase8.5-specific session-state, calibration test fixture, or a CLI-style monolith too big for direct porting. Stays on phase8.5 branch as historical artifact only.
* **REWRITE** — needed functionality but stale; write a Phase 17A-fresh module that emits the same IR adapter contract.

## Producer core modules

| file | LOC | classification | notes |
|---|---:|---|---|
| `waggledance/core/dreaming/__init__.py` | 41 | **PORT** | package init with COLLAPSE_VERDICTS + DREAMING_SCHEMA_VERSION constants |
| `waggledance/core/dreaming/curriculum.py` | 484 | **PORT** | dream night planner, emits `nights[*].target_items` matching `from_dream.adapt_dream_curriculum()` |
| `waggledance/core/dreaming/collapse.py` | 411 | **PORT** | proposal gate collapse, lazy-loads propose_solver via importlib (graceful) |
| `waggledance/core/dreaming/meta_proposal.py` | 367 | **PORT** | emits `selected_proposal` + `structurally_promising` matching `from_dream.adapt_dream_meta_proposal()` |
| `waggledance/core/dreaming/replay.py` | 367 | **PORT** | shadow replay engine, no provider calls |
| `waggledance/core/dreaming/request_pack.py` | 279 | **PORT** | dream request packaging |
| `waggledance/core/dreaming/shadow_graph.py` | 219 | **PORT** | shadow-only proposal graph |
| `waggledance/core/magma/self_model.py` | 642 | **PORT** | self-model snapshot, emits `workspace_tensions` + `blind_spots` matching `from_self_model.adapt_self_model()` |
| `waggledance/core/magma/reflective_workspace.py` | 424 | **PORT** | merges curiosity findings + self-model into tensions, imports self_model only |
| `waggledance/core/meta/__init__.py` | 77 | **PORT** | meta package init |
| `waggledance/core/meta/history.py` | 159 | **PORT** | proposal history chain |
| `waggledance/core/meta/inputs.py` | 169 | **PORT** | meta-learner input schemas |
| `waggledance/core/meta/meta_learner.py` | 591 | **PORT** | meta-learner aggregator, emits `proposals[*]` matching `from_hive.adapt_hive_proposals()` |
| `waggledance/core/meta/review_bundle.py` | 281 | **PORT** | review bundle builder, emits `proposals[*]` matching `from_hive.adapt_review_bundle()` |

**Total PORT: 14 files, ~4,511 LOC.**

## Phase 8.5 CLIs

| file | LOC | classification | notes |
|---|---:|---|---|
| `tools/gap_miner.py` | 1,439 | **LEAVE + REWRITE** | argparse + 1400 LOC of CLI scaffolding around gap-mining algorithm. Phase 17A writes a single `run_phase17a_producer_fabric_proof.py` orchestrator that imports gap-mining helpers from the ported logic OR includes a small standalone gap-miner inline. The phase8.5 monolith stays as historical artifact; its core algorithm is small enough to rewrite cleanly in v3.8 style. |
| `tools/build_self_model_snapshot.py` | 1,293 | **LEAVE + REWRITE** | similar — large CLI; the ported `waggledance/core/magma/self_model.py` exposes the build functions; Phase 17A orchestrator calls them directly. |
| `tools/dream_curriculum.py` | 298 | **LEAVE** | thin CLI wrapper over ported `waggledance/core/dreaming/curriculum.py`. Phase 17A orchestrator subsumes its function. |
| `tools/hive_proposes.py` | 276 | **LEAVE** | thin CLI wrapper over ported `waggledance/core/meta/meta_learner.py`. Phase 17A orchestrator subsumes its function. |
| `tools/run_dream_cycle.py` | (not counted) | **LEAVE** | Phase 8.5-specific local cycle harness, unneeded after Phase 13/14 runtime integration. |

**Phase 17A authors a single `tools/run_phase17a_producer_fabric_proof.py` (~400-500 LOC) that orchestrates all four producer modules end-to-end against a fixture corpus and emits a single proof JSON consumable by the existing IR adapters.**

## Phase 8.5 tests

All 10 files (~4,700 LOC) classified **LEAVE**. Reasons:

* `tests/test_gap_miner.py` (669) — tests `tools/gap_miner.py` CLI, which is not ported; rewrite Phase 17A tests against the new orchestrator.
* `tests/test_dream_curriculum.py` (459), `test_dream_meta_proposal.py` (338), `test_dream_request_pack_and_collapse.py` (511), `test_dream_shadow_replay.py` (351) — phase8.5 fixture-heavy. Phase 17A tests integrate against IR adapters end-to-end, which is the production-relevant invariant.
* `tests/test_hive_proposes.py` (585) — tests `tools/hive_proposes.py` CLI; not ported.
* `tests/test_meta_learner.py` (571) — tests meta_learner against phase8.5 input fixtures; Phase 17A tests cover end-to-end via orchestrator.
* `tests/test_reflective_workspace.py` (459), `test_self_model_calibration.py` (403), `test_self_model_snapshot.py` (389) — tests producer modules directly with phase8.5 fixtures. Phase 17A integration tests cover the same invariants via the orchestrator.

This is consistent with master prompt rule "Phase 8.5 test fixtures and snapshot calibration tests; calibration logic is phase-specific. Regression tests belong on phase8.5 branches for historical audit."

## Phase 8.5 fixtures + schemas

| file | classification | notes |
|---|---|---|
| `tests/fixtures/gap_miner_sample/*` | **LEAVE + LIGHT-PORT** | 2 small JSON fixtures. Phase 17A may copy a minimal subset under `tests/autonomy_growth/fixtures/phase17a_producers/` to seed orchestrator tests. |
| `tests/fixtures/perturbation_item.json` | **LEAVE** | dream-replay-specific fixture. |
| `schemas/review_bundle.schema.json` | **LEAVE** | Phase 8.5 schema doc. The IR adapter contracts on main are the source-of-truth for v3.8. |

## SPDX / LICENSE-CORE handling

Per master prompt rule 14: if new core/crown-jewel files are created, add SPDX headers. The IR adapters on main are tagged `# SPDX-License-Identifier: BUSL-1.1`. Producer module ports must carry the same header.

Files needing header at port time (verify each by `git show origin/phase8.5/hive-proposes:<file>` first):
* `waggledance/core/dreaming/*.py` (7 files) — likely already tagged from Phase 8.5 work
* `waggledance/core/magma/self_model.py` + `reflective_workspace.py` — verify
* `waggledance/core/meta/*.py` (5 files) — verify

If not pre-tagged, add `# SPDX-License-Identifier: BUSL-1.1` as line 1 of each ported file in the same commit. Update `LICENSE-CORE.md` to list the new core files if it tracks per-file inventories.

## Decision

**Port 14 producer module files verbatim from `origin/phase8.5/hive-proposes`. Skip 5 phase8.5 CLI monoliths and 10 phase8.5 test files. Write 1 new Phase 17A producer-fabric orchestrator + Phase 17A integration tests + 1 new 10k synthetic descriptor scale proof + scale-proof tests.**

Risk: low. Producers are pure-stdlib, IR contracts on main match producer output, no Phase 8 module dependencies. The largest unknown is whether `dreaming/collapse.py`'s lazy-import-of-propose-solver gracefully handles absent propose_solver on main; mitigation is a try/except guard.
