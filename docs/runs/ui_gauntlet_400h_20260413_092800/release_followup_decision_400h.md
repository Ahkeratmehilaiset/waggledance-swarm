# Release Decision — 400h Post-Campaign Classification

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T16:34:16+00:00
**Main ref:** `main~30`
**Total green:** 263.28h / 400h (MID-CAMPAIGN)

## Diff bucket classification

### PRODUCT (31 files)

- `configs/axioms/cottage/autumn_preparation.yaml`
- `configs/axioms/cottage/colony_food_reserves.yaml`
- `configs/axioms/cottage/colony_growth_rate.yaml`
- `configs/axioms/cottage/heating_cost.yaml`
- `configs/axioms/cottage/hive_thermal.yaml`
- `configs/axioms/cottage/honey_yield.yaml`
- `configs/axioms/cottage/nectar_flow_timing.yaml`
- `configs/axioms/cottage/pipe_freezing.yaml`
- `configs/axioms/cottage/queen_age_replacement.yaml`
- `configs/axioms/cottage/solar_yield.yaml`
- `configs/axioms/cottage/spring_inspection_timing.yaml`
- `configs/axioms/cottage/swarm_risk.yaml`
- `configs/axioms/cottage/varroa_treatment.yaml`
- `configs/axioms/cottage/varroa_treatment_calendar.yaml`
- `configs/axioms/cottage/winter_feeding_decision.yaml`
- `configs/axioms/factory/mtbf.yaml`
- `configs/axioms/factory/oee.yaml`
- `configs/axioms/gadget/battery.yaml`
- `configs/axioms/gadget/signal_strength.yaml`
- `configs/axioms/home/comfort_energy.yaml`
- `configs/axioms/home/heat_pump_cop.yaml`
- `configs/axioms/home/indoor_air_quality.yaml`
- `configs/settings.yaml`
- `core/chat_routing_engine.py`
- `waggledance/application/services/chat_service.py`
- `waggledance/application/services/hybrid_retrieval_service.py`
- `waggledance/bootstrap/container.py`
- `waggledance/core/learning/embedding_cache.py`
- `waggledance/core/reasoning/hybrid_observer.py`
- `waggledance/core/reasoning/hybrid_router.py`
- … and 1 more

### TEST_HARNESS (63 files)

- `docs/runs/RESUME_HERE.md`
- `docs/runs/hybrid_shadow_three_way_2026-04-23T085142Z.json`
- `docs/runs/hybrid_shadow_three_way_2026-04-23T085142Z.md`
- `docs/runs/hybrid_shadow_three_way_2026-04-23T093413Z.json`
- `docs/runs/hybrid_shadow_three_way_2026-04-23T093413Z.md`
- `docs/runs/hybrid_shadow_three_way_2026-04-23T102734Z.json`
- `docs/runs/hybrid_shadow_three_way_2026-04-23T102734Z.md`
- `docs/runs/hybrid_shadow_three_way_2026-04-23T102843Z.json`
- `docs/runs/hybrid_shadow_three_way_2026-04-23T102843Z.md`
- `docs/runs/hybrid_shadow_three_way_2026-04-23T133253Z.json`
- `docs/runs/hybrid_shadow_three_way_2026-04-23T133253Z.md`
- `docs/runs/magma_hybrid_candidate_trace.jsonl`
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
- … and 33 more

### DOCS_NARRATIVE (8 files)

- `docs/plans/phase_A6_determinism_results.json`
- `docs/plans/phase_A_preflight_report_2026-04-23.md`
- `docs/plans/phase_A_preflight_results.json`
- `docs/plans/phase_B6_disaster_recovery_results.json`
- `docs/plans/phase_B_completion_report_2026-04-23.md`
- `docs/plans/phase_C_analysis_and_DEF_handoff_2026-04-23.md`
- `docs/plans/phase_D_decision_2026-04-23.md`
- `docs/plans/phase_D_decision_v2_2026-04-23.md`

### CI_WORKFLOW (0 files)


### VERSION (0 files)


### OTHER (10 files)

- `backup/2026-04-23/faiss_pre-hybrid.tar.gz`
- `backup/2026-04-23/settings.yaml.pre-hybrid`
- `tools/analyze_hybrid_candidate_trace.py`
- `tools/backfill_axioms_to_hex.py`
- `tools/campaign_watchdog.py`
- `tools/compute_cell_centroids.py`
- `tools/hex_manifest.py`
- `tools/migrate_embedding_model.py`
- `tools/shadow_route_three_way.py`
- `tools/upgrade_axioms_for_v3.py`

## Gate checks (x.txt rule 5 + Phase 9)

- Campaign complete (>= 400h): no (263.3h)
- XSS hits: 0 (target 0)
- DOM breaks: 0 (target 0)
- PRODUCT diff: non-empty

## Proposed PATH

**NO_RELEASE_FAILURE_REPORT**

PRODUCT diff is non-empty but gates are not green. Per x.txt Phase 9:
- no bump, no tag, no release
- write failure handoff
- still sync docs truthfully