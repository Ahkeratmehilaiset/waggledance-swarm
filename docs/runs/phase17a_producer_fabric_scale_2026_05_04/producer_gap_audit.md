# Phase 17A — Producer Gap Audit (P2)

**Date:** 2026-05-04
**Audit subject:** what producer-side code is missing from `main` (v3.8.0 stable) such that the IR consumer adapters (`from_curiosity.py`, `from_dream.py`, `from_hive.py`, `from_self_model.py`) currently have no real data source on `main`.

## Summary

| layer | on main | on phase8.5 branches | gap |
|---|---|---|---|
| **IR consumer adapters** | ✅ all 4 present | (also present) | none — main is canonical |
| **Curiosity producer** (gap mining) | ❌ absent | `tools/gap_miner.py` (1439 LOC, stdlib-only CLI) | full producer missing |
| **Self-model producer** | ❌ absent | `waggledance/core/magma/self_model.py` (642 LOC) + `reflective_workspace.py` (424 LOC) | full producer missing |
| **Dream curriculum producer** | ❌ absent | `waggledance/core/dreaming/*` (7 modules, 2168 LOC) | full producer missing |
| **Hive proposes / meta-learner** | ❌ absent | `waggledance/core/meta/*` (5 modules, 1277 LOC) + `tools/hive_proposes.py` (276 LOC) | full producer missing |

Total missing on main: **~6500 LOC of pure-stdlib producer logic** that exists, was tested, and emits exactly the JSON shapes consumed by the IR adapters that are already shipped on main.

## Why the gap exists

Phase 9 squash-merge (PR #51) ships the **CONSUMER side** of the autonomy fabric (IR + adapters + downstream pipeline) but **deferred the PRODUCER side** to follow-up PRs (`docs/runs/local_only_branch_audit.md` Phase 1 audit, 2026-04-27). The Phase 9 release was made self-contained by accepting that producers can land later without changing main contracts; the IR adapters define the schema.

The follow-up PRs never landed:

| originally planned PR | actual status |
|---|---|
| `phase8.5/vector-chaos → main` (R7.5 Vector Writer Resilience) | not opened, branch local-only until Phase 17A P1 |
| `phase8.5/curiosity-organ → main` (Session A — Curiosity Organ) | not opened |
| `phase8.5/self-model-layer → main` (Session B — Self-Model Layer) | not opened |
| `phase8.5/dream-curriculum → main` (Session C — Dream Pipeline) | not opened |
| `phase8.5/hive-proposes → main` (Session D — The Hive Proposes) | not opened |

Phase 17A closes this gap.

## What main currently CAN do without producers

* IR adapters compile and have unit tests (the adapter contracts are exercised against fixture JSON in `tests/test_phase9_cognition_ir.py`).
* Phase 11–16D autonomy_growth lane operates: 104 canonical seeds → auto-promotion → capability lookup, runtime hint extraction, upstream structured_request derivation, full restart continuity.
* Reality View renders 5/11 panels populated against fixture data; 6/11 honestly marked unavailable (`docs/runs/phase9_reality_view_render.json`).

## What main currently CANNOT do without producers

* The autonomy_growth lane consumes **canonical seeds** (curated by hand, 104 entries) but has no real **gap-mining ingestion**: it cannot identify what queries are unsolved, cluster them by suspected_gap_type, or rank them by estimated_value. (gap_miner.py is the missing module.)
* The system cannot maintain a **self-model snapshot** that summarizes its own capability/weakness/uncertainty grounded in real artifacts. (self_model.py is missing.)
* The system cannot run a **dream curriculum** of counterfactual rehearsals on synthetic trajectories. (`waggledance/core/dreaming/` missing.)
* The system cannot **aggregate proposals across observations** via meta-learner and emit hive-style consensus proposals. (`waggledance/core/meta/meta_learner.py` missing.)
* Reality View cannot populate the `self_model_summary`, `dream_targets`, `hive_proposals`, or `tensions` panels with real producer output — they remain `available=false` with structured rationale.

## Why the producers are safely portable

1. **Pure-stdlib imports.** Verified via `git show origin/phase8.5/hive-proposes:<file> | grep -E '^(import|from)'`:
   * `gap_miner.py`: `argparse, hashlib, json, re, sys, collections, dataclasses, datetime, pathlib, typing` only
   * `self_model.py`: `hashlib, json, re` only
   * `reflective_workspace.py`: `hashlib, json, re, collections, dataclasses, pathlib, typing` only
   * `meta_learner.py`: `hashlib, json, re, dataclasses, typing` + relative imports from `waggledance.core.meta` siblings only
   * `curriculum.py`: `hashlib, json, math, re, dataclasses, datetime, pathlib, typing` only
   * `collapse.py`: `importlib.util, json, sys, dataclasses, pathlib, typing` + relative imports from sibling
   * `hive_proposes.py` (CLI): `argparse, json, sys, datetime, pathlib` + relative imports
2. **No torch, no faiss, no chromadb, no provider HTTP.** Aligns with master prompt rule 12 (provider/builder delta = 0).
3. **No imports of refactored Phase 8 modules.** Phase 9 squash didn't refactor anything the producers depend on; it only added the consumer side.
4. **IR adapter contracts match producer output JSON.** Verified by reading both:
   * `from_curiosity.adapt_curiosity_log()` consumes rows with `candidate_cell`, `curiosity_id`, `suspected_gap_type`, `estimated_value`, `count`, `fallback_rate` — gap_miner emits this.
   * `from_self_model.adapt_self_model()` consumes `workspace_tensions` + `blind_spots` with severity, lifecycle_status, evidence_refs — self_model.py emits this.
   * `from_dream.adapt_dream_curriculum()` consumes `nights[*].target_items` + uncertainty — curriculum.py emits this.
   * `from_dream.adapt_dream_meta_proposal()` consumes `selected_proposal` + `structurally_promising` — meta_proposal.py emits this.
   * `from_hive.adapt_hive_proposals()` consumes `proposals[*]` with proposal_type, scope_class, expected_value etc. — meta_learner emits this; `hive_proposes.py` CLI bundles it.
5. **Deterministic + offline.** Producers operate on input JSON/JSONL files and emit output JSON. No clocks (other than emission timestamp), no network, no actuators.

## Risk surface for porting

| risk | severity | mitigation |
|---|---|---|
| Phase 8.5 tests assume Phase 8.5-specific session-state fixtures absent on main | medium | port only producer modules, NOT phase8.5 tests; write Phase 17A integration tests fresh |
| `dreaming/collapse.py` lazy-loads `propose_solver.py` via importlib | low | `propose_solver.py` is shadow-only — wrap call in try/except to degrade gracefully |
| Phase 8.5 producer files lack SPDX headers | low | add `# SPDX-License-Identifier: BUSL-1.1` to each ported file in same commit |
| Phase 8.5 producer files use names that may clash with main modules | low | grep main for `dreaming/`, `magma/self_model.py`, `meta/` — if absent, no clash |
| `tools/gap_miner.py` (1439 LOC monolithic CLI) is too big for direct port without integration test | medium | port as-is; write a thin Phase 17A entry-point that imports the gap_miner functions but skips the CLI argparse layer |
| Sheer code volume (~6500 LOC port) may break review window | medium | one PR with logical commit groups: chore(producers): port modules / feat(phase17a): proof tool / feat(scale): 10k descriptors / docs |

## Decision

**Port the core producer modules (`waggledance/core/dreaming/*`, `waggledance/core/magma/self_model.py + reflective_workspace.py`, `waggledance/core/meta/*`) directly from `origin/phase8.5/hive-proposes` to `main` via Phase 17A branch. Skip Phase 8.5 CLIs (gap_miner.py, build_self_model_snapshot.py, dream_curriculum.py CLI, hive_proposes.py CLI) — write a single Phase 17A proof tool instead that calls the producer modules directly.**

This is the master prompt's "If safe branch code exists, port it into current architecture" path with the "if branch code is too stale" minor variation: the modules port directly, but the standalone CLIs (1500+ LOC each) are phase8.5-style monoliths that are more usefully replaced by one Phase 17A purpose-built orchestrator.

Total new code on main:
* **Ported (verbatim)**: ~4,500 LOC across 14 files (dreaming/* 7 files, magma/{self_model, reflective_workspace}.py 2 files, meta/* 5 files).
* **NOT ported**: ~3,300 LOC of phase8.5 CLI monoliths (gap_miner.py, build_self_model_snapshot.py, dream_curriculum.py CLI wrapper, hive_proposes.py CLI wrapper, run_dream_cycle.py).
* **NOT ported**: ~4,700 LOC of phase8.5 tests (kept on phase8.5 branches as historical audit).
* **NEW Phase 17A code**: `tools/run_phase17a_producer_fabric_proof.py` (~400-500 LOC orchestrator), `tests/autonomy_growth/test_phase17a_producer_fabric_proof.py` (~300-500 LOC integration tests).
* **NEW Phase 17A scale code**: `tools/run_solver_scale_proof.py` (~300-400 LOC), `tests/autonomy_growth/test_solver_scale_proof.py` (~150-300 LOC).
* **OPTIONAL Phase 17A seed corpus growth**: +24 entries in `low_risk_seed_library.py` (~+200 LOC, no allowlist widening).

Total expected Phase 17A diff on main: ~5,500-6,500 LOC (mostly ported, minority new).
