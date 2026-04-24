# 400h Campaign — Incident Matrix (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-24T12:07:11+00:00

## By (category, mode)

| Category | Mode | Count |
|---|---|---|
| chat_response_failure | WARM | 1944 |
| health_failure | COLD | 218 |
| chat_failure | COLD | 121 |
| cycle_crash_recovery | WARM | 23 |
| backend_unhealthy | HOT | 21 |
| auth_recovery_failure | WARM | 15 |
| chat_send_failure | WARM | 13 |
| tab_switch_failure | WARM | 6 |
| chat_failure | HOT | 4 |
| cookie_failure | COLD | 3 |
| context_recycle_failure | HOT | 2 |
| context_recycle_failure | WARM | 2 |
| auth_bootstrap_failure | WARM | 1 |

## Product defects (zero target)

- Count: **0**

## Harness defects (fixed during campaign)

- See `docs/runs/campaign_hardening_log.md` for the chronological root-cause narrative. Each harness bug has a commit SHA.

## CI/Workflow incidents

- See `CHANGELOG.md` [Unreleased] block. Tests + WaggleDance CI both green on main since commit `c7f6201` (2026-04-20).
