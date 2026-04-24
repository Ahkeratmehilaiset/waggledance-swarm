# Release Decision — 400h Post-Campaign Classification

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-24T03:36:10+00:00
**Main ref:** `main~30`
**Total green:** 287.30h / 400h (MID-CAMPAIGN)

## Diff bucket classification

### PRODUCT (8 files)

- `configs/axioms/cottage/autumn_preparation.yaml`
- `configs/axioms/cottage/colony_growth_rate.yaml`
- `configs/axioms/cottage/nectar_flow_timing.yaml`
- `configs/axioms/cottage/queen_age_replacement.yaml`
- `configs/axioms/cottage/spring_inspection_timing.yaml`
- `configs/axioms/cottage/varroa_treatment_calendar.yaml`
- `configs/axioms/cottage/winter_feeding_decision.yaml`
- `configs/axioms/home/indoor_air_quality.yaml`

### TEST_HARNESS (32 files)

- `docs/runs/hybrid_shadow_three_way_2026-04-23T133253Z.json`
- `docs/runs/hybrid_shadow_three_way_2026-04-23T133253Z.md`
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
- `docs/runs/ui_gauntlet_400h_20260413_092800/daily_summary_day_012.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/final_400h_incident_matrix.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/final_400h_reliability.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/final_400h_summary.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/final_findings.json`
- `docs/runs/ui_gauntlet_400h_20260413_092800/hot_results.jsonl`
- `docs/runs/ui_gauntlet_400h_20260413_092800/incident_log.jsonl`
- `docs/runs/ui_gauntlet_400h_20260413_092800/release_followup_decision_400h.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/segment_metrics_059.json`
- `docs/runs/ui_gauntlet_400h_20260413_092800/segment_metrics_060.json`
- `docs/runs/ui_gauntlet_400h_20260413_092800/segment_metrics_061.json`
- `docs/runs/ui_gauntlet_400h_20260413_092800/segment_metrics_062.json`
- `docs/runs/ui_gauntlet_400h_20260413_092800/segment_report_059.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/segment_report_060.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/segment_report_061.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/segment_report_062.md`
- … and 2 more

### DOCS_NARRATIVE (0 files)


### CI_WORKFLOW (0 files)


### VERSION (0 files)


### OTHER (3 files)

- `tools/analyze_hybrid_candidate_trace.py`
- `tools/backfill_axioms_to_hex.py`
- `tools/campaign_watchdog.py`

## Gate checks (x.txt rule 5 + Phase 9)

- Campaign complete (>= 400h): no (287.3h)
- XSS hits: 0 (target 0)
- DOM breaks: 0 (target 0)
- PRODUCT diff: non-empty

## Proposed PATH

**NO_RELEASE_FAILURE_REPORT**

PRODUCT diff is non-empty but gates are not green. Per x.txt Phase 9:
- no bump, no tag, no release
- write failure handoff
- still sync docs truthfully