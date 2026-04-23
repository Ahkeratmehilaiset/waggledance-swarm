# Release Decision — 400h Post-Campaign Classification

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T09:10:15+00:00
**Main ref:** `main~30`
**Total green:** 243.27h / 400h (MID-CAMPAIGN)

## Diff bucket classification

### PRODUCT (23 files)

- `configs/axioms/cottage/colony_food_reserves.yaml`
- `configs/axioms/cottage/heating_cost.yaml`
- `configs/axioms/cottage/hive_thermal.yaml`
- `configs/axioms/cottage/honey_yield.yaml`
- `configs/axioms/cottage/pipe_freezing.yaml`
- `configs/axioms/cottage/solar_yield.yaml`
- `configs/axioms/cottage/swarm_risk.yaml`
- `configs/axioms/cottage/varroa_treatment.yaml`
- `configs/axioms/factory/mtbf.yaml`
- `configs/axioms/factory/oee.yaml`
- `configs/axioms/gadget/battery.yaml`
- `configs/axioms/gadget/signal_strength.yaml`
- `configs/axioms/home/comfort_energy.yaml`
- `configs/axioms/home/heat_pump_cop.yaml`
- `waggledance/application/services/parallel_llm_dispatcher.py`
- `waggledance/core/autonomy/lifecycle.py`
- `waggledance/core/learning/case_builder.py`
- `waggledance/core/learning/dream_mode.py`
- `waggledance/core/learning/embedding_cache.py`
- `waggledance/core/learning/prediction_error_ledger.py`
- `waggledance/core/learning/solver_hash.py`
- `waggledance/core/orchestration/round_table.py`
- `waggledance/core/reasoning/question_frame.py`

### TEST_HARNESS (58 files)

- `docs/runs/RESUME_HERE.md`
- `docs/runs/campaign_hardening_log.md`
- `docs/runs/hybrid_shadow_three_way_2026-04-23T085142Z.json`
- `docs/runs/hybrid_shadow_three_way_2026-04-23T085142Z.md`
- `docs/runs/release_followup_final_400h.md`
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
- `docs/runs/ui_gauntlet_400h_20260413_092800/final_400h_incident_matrix.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/final_400h_reliability.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/final_400h_summary.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/final_findings.json`
- `docs/runs/ui_gauntlet_400h_20260413_092800/hot_results.jsonl`
- `docs/runs/ui_gauntlet_400h_20260413_092800/incident_log.jsonl`
- `docs/runs/ui_gauntlet_400h_20260413_092800/release_followup_decision_400h.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/segment_metrics_038.json`
- `docs/runs/ui_gauntlet_400h_20260413_092800/segment_metrics_039.json`
- `docs/runs/ui_gauntlet_400h_20260413_092800/segment_metrics_040.json`
- `docs/runs/ui_gauntlet_400h_20260413_092800/segment_metrics_041.json`
- `docs/runs/ui_gauntlet_400h_20260413_092800/segment_metrics_042.json`
- `docs/runs/ui_gauntlet_400h_20260413_092800/segment_metrics_043.json`
- … and 28 more

### DOCS_NARRATIVE (40 files)

- `CHANGELOG.md`
- `CURRENT_STATE.md`
- `CURRENT_STATUS.md`
- `README.md`
- `docs/HYBRID_RETRIEVAL.md`
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
- `docs/cells/thermal/MANIFEST.md`
- `docs/cells/thermal/manifest.json`
- `docs/plans/GPT.txt`
- `docs/plans/GPT_response2.txt`
- `docs/plans/GPT_response3.txt`
- `docs/plans/GPT_review_round2_prompt.md`
- `docs/plans/GPT_review_round3_prompt.md`
- `docs/plans/gemini.txt`
- `docs/plans/grock.txt`
- `docs/plans/hex_topology_performance_metrics_2026-04-23.md`
- … and 10 more

### CI_WORKFLOW (1 files)

- `.github/workflows/ci.yml`

### VERSION (1 files)

- `Dockerfile`

### OTHER (16 files)

- `.gitignore`
- `backup/2026-04-23/faiss_pre-hybrid.tar.gz`
- `backup/2026-04-23/settings.yaml.pre-hybrid`
- `tools/backfill_axioms_to_hex.py`
- `tools/benchmark_harness.py`
- `tools/campaign_auto_commit.py`
- `tools/campaign_reports.py`
- `tools/campaign_watchdog.py`
- `tools/cell_manifest.py`
- `tools/compute_cell_centroids.py`
- `tools/hex_manifest.py`
- `tools/migrate_embedding_model.py`
- `tools/run_benchmark.py`
- `tools/runtime_shadow_compare.py`
- `tools/shadow_route_three_way.py`
- `tools/upgrade_axioms_for_v3.py`

## Gate checks (x.txt rule 5 + Phase 9)

- Campaign complete (>= 400h): no (243.3h)
- XSS hits: 0 (target 0)
- DOM breaks: 0 (target 0)
- PRODUCT diff: non-empty

## Proposed PATH

**NO_RELEASE_FAILURE_REPORT**

PRODUCT diff is non-empty but gates are not green. Per x.txt Phase 9:
- no bump, no tag, no release
- write failure handoff
- still sync docs truthfully