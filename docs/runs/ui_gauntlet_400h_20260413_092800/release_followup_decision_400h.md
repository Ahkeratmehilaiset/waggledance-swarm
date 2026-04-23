# Release Decision — 400h Post-Campaign Classification

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T11:10:35+00:00
**Main ref:** `main~30`
**Total green:** 243.27h / 400h (MID-CAMPAIGN)

## Diff bucket classification

### PRODUCT (21 files)

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
- `configs/settings.yaml`
- `waggledance/application/services/hybrid_retrieval_service.py`
- `waggledance/bootstrap/container.py`
- `waggledance/core/learning/embedding_cache.py`
- `waggledance/core/learning/solver_hash.py`
- `waggledance/core/reasoning/hybrid_router.py`
- `waggledance/core/reasoning/question_frame.py`

### TEST_HARNESS (76 files)

- `docs/runs/RESUME_HERE.md`
- `docs/runs/hybrid_shadow_three_way_2026-04-23T085142Z.json`
- `docs/runs/hybrid_shadow_three_way_2026-04-23T085142Z.md`
- `docs/runs/hybrid_shadow_three_way_2026-04-23T093413Z.json`
- `docs/runs/hybrid_shadow_three_way_2026-04-23T093413Z.md`
- `docs/runs/hybrid_shadow_three_way_2026-04-23T102734Z.json`
- `docs/runs/hybrid_shadow_three_way_2026-04-23T102734Z.md`
- `docs/runs/hybrid_shadow_three_way_2026-04-23T102843Z.json`
- `docs/runs/hybrid_shadow_three_way_2026-04-23T102843Z.md`
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
- … and 46 more

### DOCS_NARRATIVE (37 files)

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
- `docs/plans/hybrid_retrieval_activation_refined_2026-04-23.md`
- `docs/plans/hybrid_retrieval_activation_review_2026-04-23.md`
- `docs/plans/hybrid_retrieval_activation_v3_1_amendments_2026-04-23.md`
- `docs/plans/hybrid_retrieval_activation_v3_2026-04-23.md`
- `docs/plans/phase_A6_determinism_results.json`
- … and 7 more

### CI_WORKFLOW (0 files)


### VERSION (0 files)


### OTHER (11 files)

- `.gitignore`
- `backup/2026-04-23/faiss_pre-hybrid.tar.gz`
- `backup/2026-04-23/settings.yaml.pre-hybrid`
- `tools/backfill_axioms_to_hex.py`
- `tools/campaign_auto_commit.py`
- `tools/cell_manifest.py`
- `tools/compute_cell_centroids.py`
- `tools/hex_manifest.py`
- `tools/migrate_embedding_model.py`
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