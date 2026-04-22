# Release Decision — 400h Post-Campaign Classification

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-22T22:53:08+00:00
**Main ref:** `main~30`
**Total green:** 215.24h / 400h (MID-CAMPAIGN)

## Diff bucket classification

### PRODUCT (35 files)

- `configs/settings.yaml`
- `waggledance/adapters/cli/start_runtime.py`
- `waggledance/adapters/config/settings_loader.py`
- `waggledance/adapters/feeds/__init__.py`
- `waggledance/adapters/feeds/feed_ingest_sink.py`
- `waggledance/adapters/http/api.py`
- `waggledance/adapters/http/middleware/auth.py`
- `waggledance/adapters/http/routes/chat.py`
- `waggledance/adapters/http/routes/compat_dashboard.py`
- `waggledance/adapters/http/routes/hologram.py`
- `waggledance/adapters/http/routes/metrics.py`
- `waggledance/adapters/http/routes/status.py`
- `waggledance/application/services/chat_service.py`
- `waggledance/application/services/hex_neighbor_assist.py`
- `waggledance/application/services/parallel_llm_dispatcher.py`
- `waggledance/bootstrap/container.py`
- `waggledance/core/autonomy/lifecycle.py`
- `waggledance/core/domain/hex_mesh.py`
- `waggledance/core/learning/case_builder.py`
- `waggledance/core/learning/dream_mode.py`
- `waggledance/core/learning/prediction_error_ledger.py`
- `waggledance/core/orchestration/round_table.py`
- `waggledance/core/priority_lock.py`
- `waggledance/observatory/__init__.py`
- `waggledance/observatory/mama_events/__init__.py`
- `waggledance/observatory/mama_events/ablations.py`
- `waggledance/observatory/mama_events/caregiver_binding.py`
- `waggledance/observatory/mama_events/consolidation.py`
- `waggledance/observatory/mama_events/contamination.py`
- `waggledance/observatory/mama_events/gate.py`
- … and 5 more

### TEST_HARNESS (118 files)

- `docs/runs/campaign_hardening_log.md`
- `docs/runs/mama_event_overnight_closeout_20260410.md`
- `docs/runs/release_followup_decision.md`
- `docs/runs/release_followup_final.md`
- `docs/runs/release_followup_final_400h.md`
- `docs/runs/release_followup_index.md`
- `docs/runs/release_followup_truth.md`
- `docs/runs/release_followup_validation.md`
- `docs/runs/release_polish_live_closeout_20260409.md`
- `docs/runs/ui_gauntlet_20260412/_launch_gauntlet_server.py`
- `docs/runs/ui_gauntlet_20260412/fault_drills.md`
- `docs/runs/ui_gauntlet_20260412/findings.json`
- `docs/runs/ui_gauntlet_20260412/mixed_soak.md`
- `docs/runs/ui_gauntlet_20260412/plan.md`
- `docs/runs/ui_gauntlet_20260412/summary.md`
- `docs/runs/ui_gauntlet_20260412/ui_fidelity_baseline.md`
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
- `docs/runs/ui_gauntlet_400h_20260413_092800/dryrun_results.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/fault_drills.md`
- `docs/runs/ui_gauntlet_400h_20260413_092800/final_400h_incident_matrix.md`
- … and 88 more

### DOCS_NARRATIVE (18 files)

- `AGENTS.md`
- `CHANGELOG.md`
- `CLAUDE.md`
- `CURRENT_STATE.md`
- `CURRENT_STATUS.md`
- `README.md`
- `RECOVERY_RECONSTRUCTION_REPORT.md`
- `docs/API.md`
- `docs/HYBRID_RETRIEVAL.md`
- `docs/RECOVERY_POLICY.md`
- `reports/observatory/MAMA_EVENT_ABLATIONS.md`
- `reports/observatory/MAMA_EVENT_BASELINE.md`
- `reports/observatory/MAMA_EVENT_CANDIDATES.md`
- `reports/observatory/MAMA_EVENT_FRAMEWORK.md`
- `reports/observatory/MAMA_EVENT_GATE.md`
- `reports/observatory/MAMA_EVENT_LONGRUN.md`
- `reports/observatory/MAMA_EVENT_OVERNIGHT.md`
- `reports/observatory/MAMA_EVENT_OVERNIGHT_RESUME.md`

### CI_WORKFLOW (1 files)

- `.github/workflows/ci.yml`

### VERSION (3 files)

- `Dockerfile`
- `pyproject.toml`
- `waggledance/__init__.py`

### OTHER (21 files)

- `.gitignore`
- `manifest.json`
- `requirements.lock.txt`
- `start_waggledance.py`
- `tools/benchmark_harness.py`
- `tools/campaign_auto_commit.py`
- `tools/campaign_reports.py`
- `tools/campaign_watchdog.py`
- `tools/mama_event_longrun.py`
- `tools/mama_event_longrun_analysis.py`
- `tools/mama_event_overnight.py`
- `tools/mama_event_overnight_analysis.py`
- `tools/mama_event_report.py`
- `tools/restore.py`
- `tools/run_benchmark.py`
- `tools/runtime_autotune_30h.py`
- `tools/runtime_shadow_compare.py`
- `tools/runtime_soak_30h.py`
- `tools/savepoint.ps1`
- `tools/waggle_backup.py`
- `tools/waggle_restore.py`

## Gate checks (x.txt rule 5 + Phase 9)

- Campaign complete (>= 400h): no (215.2h)
- XSS hits: 0 (target 0)
- DOM breaks: 0 (target 0)
- PRODUCT diff: non-empty

## Proposed PATH

**NO_RELEASE_FAILURE_REPORT**

PRODUCT diff is non-empty but gates are not green. Per x.txt Phase 9:
- no bump, no tag, no release
- write failure handoff
- still sync docs truthfully