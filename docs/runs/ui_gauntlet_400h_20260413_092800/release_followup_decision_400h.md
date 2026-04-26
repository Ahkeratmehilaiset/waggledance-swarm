# Release Decision — 400h Post-Campaign Classification

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-26T13:44:52+00:00
**Main ref:** `main~30`
**Total green:** 415.34h / 400h (FINAL)

## Diff bucket classification

### PRODUCT (12 files)

- `waggledance/core/dreaming/__init__.py`
- `waggledance/core/dreaming/collapse.py`
- `waggledance/core/dreaming/curriculum.py`
- `waggledance/core/dreaming/meta_proposal.py`
- `waggledance/core/dreaming/replay.py`
- `waggledance/core/dreaming/request_pack.py`
- `waggledance/core/dreaming/shadow_graph.py`
- `waggledance/core/learning/composition_graph.py`
- `waggledance/core/learning/solver_hash.py`
- `waggledance/core/magma/reflective_workspace.py`
- `waggledance/core/magma/self_model.py`
- `waggledance/core/magma/vector_events.py`

### TEST_HARNESS (120 files)

- `docs/runs/dream/0892a89517f2/dream_curriculum.json`
- `docs/runs/dream/0892a89517f2/dream_curriculum.md`
- `docs/runs/dream/0892a89517f2/proposal_collapse_report.json`
- `docs/runs/dream/0892a89517f2/proposal_collapse_report.md`
- `docs/runs/dream/0892a89517f2/request_packs/dream_request_pack_night_01.json`
- `docs/runs/dream/0892a89517f2/request_packs/dream_request_pack_night_01.md`
- `docs/runs/dream/0892a89517f2/shadow_replay_report.json`
- `docs/runs/dream/0892a89517f2/shadow_replay_report.md`
- `docs/runs/hex_subdivision_plan.md`
- `docs/runs/honeycomb_400h/plan.md`
- `docs/runs/phase8_5_curiosity_session_state.json`
- `docs/runs/phase8_5_dream_session_state.json`
- `docs/runs/phase8_5_self_model_session_state.json`
- `docs/runs/phase8_capability_report.md`
- `docs/runs/phase8_ci_baseline.md`
- `docs/runs/phase8_validation.md`
- `docs/runs/solver_composition_report.md`
- `docs/runs/solver_dedupe_report.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/campaign_state.json`
- `docs/runs/ui_gauntlet_400h_20260413_092800/daily_summary_day_001.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/daily_summary_day_002.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/daily_summary_day_003.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/daily_summary_day_004.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/daily_summary_day_005.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/daily_summary_day_006.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/daily_summary_day_007.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/daily_summary_day_008.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/daily_summary_day_009.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/daily_summary_day_010.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/daily_summary_day_011.md`
- … and 90 more

### DOCS_NARRATIVE (38 files)

- `CHANGELOG.md`
- `README.md`
- `docs/architecture/DREAM_MODE_2_0.md`
- `docs/architecture/DREAM_PROVENANCE.md`
- `docs/architecture/DREAM_REPLAY_FORMULAS.md`
- `docs/architecture/GAP_MINER_VISION.md`
- `docs/architecture/HONEYCOMB_SOLVER_SCALING.md`
- `docs/architecture/HOOKS_FOR_DREAM_CURRICULUM.md`
- `docs/architecture/HOOKS_FOR_META_LEARNER.md`
- `docs/architecture/MAGMA_FAISS_SCALING.md`
- `docs/architecture/MAGMA_VECTOR_STAGE2.md`
- `docs/architecture/PHASE8_METRICS.md`
- `docs/architecture/SELF_MODEL_FORMULAS.md`
- `docs/architecture/SELF_MODEL_LAYER.md`
- `docs/architecture/SELF_MODEL_PROVENANCE.md`
- `docs/cells/INDEX.md`
- `docs/cells/energy/MANIFEST.md`
- `docs/cells/energy/manifest.json`
- `docs/cells/general/MANIFEST.md`
- `docs/cells/general/manifest.json`
- `docs/cells/learning/MANIFEST.md`
- `docs/cells/learning/manifest.json`
- `docs/cells/math/MANIFEST.md`
- `docs/cells/math/manifest.json`
- `docs/cells/safety/MANIFEST.md`
- `docs/cells/safety/manifest.json`
- `docs/cells/seasonal/MANIFEST.md`
- `docs/cells/seasonal/manifest.json`
- `docs/cells/system/MANIFEST.md`
- `docs/cells/system/manifest.json`
- … and 8 more

### CI_WORKFLOW (0 files)


### VERSION (0 files)


### OTHER (17 files)

- `LICENSE-BUSL.txt`
- `schemas/solver_proposal.schema.json`
- `tools/backfill_axioms_to_hex.py`
- `tools/build_self_model_snapshot.py`
- `tools/campaign_watchdog.py`
- `tools/cell_manifest.py`
- `tools/dream_curriculum.py`
- `tools/gap_miner.py`
- `tools/hex_subdivision_plan.py`
- `tools/migrate_to_vector_root.py`
- `tools/phase8_capability_report.py`
- `tools/propose_solver.py`
- `tools/run_dream_cycle.py`
- `tools/run_honeycomb_400h_campaign.py`
- `tools/solver_composition_report.py`
- `tools/solver_dedupe.py`
- `tools/vector_indexer.py`

## Gate checks (x.txt rule 5 + Phase 9)

- Campaign complete (>= 400h): yes
- XSS hits: 0 (target 0)
- DOM breaks: 0 (target 0)
- PRODUCT diff: non-empty

## Proposed PATH

**NEW_PATCH_RELEASE**

PRODUCT diff is non-empty and all gates are green. Per x.txt Phase 9:
- bump patch version
- update changelog under new released version
- commit release bump separately
- tag annotated
- merge to main with --no-ff
- push branch + main + tag
- create new GitHub release
- run backup tool
- write release handoff