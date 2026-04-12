# Release Follow-up Index — v3.5.7

All reports related to the v3.5.7 release and post-release hardening.

## Release Pipeline

| Phase | Report | Date | Result |
|---|---|---|---|
| Release gate | `C:/WaggleDance_ReleaseFinalRun/20260410_031819/reports/FINAL_RELEASE_GATE.md` | 2026-04-10 | RC (soak short) |
| Overnight soak | `C:/WaggleDance_ReleaseFinalRun/20260410_031819/reports/OVERNIGHT_SOAK_FINAL.md` | 2026-04-10 | PASS w/caveats |
| Phase 7 fixes | `C:/WaggleDance_ReleaseFinalRun/20260410_031819/reports/PHASE7_FIXES.md` | 2026-04-10 | 3 fixes, 20 tests |
| Phase 9 verify | `C:/WaggleDance_ReleaseFinalRun/20260410_031819/reports/PHASE9_FINAL_VERIFY.md` | 2026-04-10 | 5378/0 tests, 15/15 endpoints |
| 12h soak recheck | [overnight_soak_recheck.md](overnight_soak_recheck.md) | 2026-04-12 | 358/358 green |
| Smoke recheck | [smoke_recheck_latest.md](smoke_recheck_latest.md) | 2026-04-12 | 10/10 endpoints |
| Promotion | [promotion_latest.md](promotion_latest.md) | 2026-04-12 | project2_new→project2 |
| Final handoff | [final_release_handoff.md](final_release_handoff.md) | 2026-04-12 | v3.5.7 shipped |

## Post-Release Hardening (UI Gauntlet)

| Phase | Report | Result |
|---|---|---|
| Plan | [ui_gauntlet_20260412/plan.md](ui_gauntlet_20260412/plan.md) | — |
| A: Baseline | — | 4/4 checks pass |
| B: UI fidelity | [ui_gauntlet_20260412/ui_fidelity_baseline.md](ui_gauntlet_20260412/ui_fidelity_baseline.md) | 33/33 pass |
| C: Chat gauntlet | `ui_gauntlet_20260412/chat_ui_results.jsonl` | 466/466, 0 XSS |
| D: Fault drills | [ui_gauntlet_20260412/fault_drills.md](ui_gauntlet_20260412/fault_drills.md) | 6/7 pass |
| E: Mixed soak | [ui_gauntlet_20260412/mixed_soak.md](ui_gauntlet_20260412/mixed_soak.md) | 30 min stable |
| Summary | [ui_gauntlet_20260412/summary.md](ui_gauntlet_20260412/summary.md) | All gates PASS |
| Findings | `ui_gauntlet_20260412/findings.json` | 0 hotfixes needed |

## Follow-up Docs Sync

| Report | Purpose |
|---|---|
| [release_followup_truth.md](release_followup_truth.md) | Phase 0 truth intake |
| [release_followup_decision.md](release_followup_decision.md) | Phase 1 diff classification + path decision |
| [release_followup_validation.md](release_followup_validation.md) | Phase 3 test results |
| [release_followup_final.md](release_followup_final.md) | Phase 5 final handoff |
