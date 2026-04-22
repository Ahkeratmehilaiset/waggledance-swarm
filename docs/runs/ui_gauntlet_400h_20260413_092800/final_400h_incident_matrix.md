# 400h Campaign — Incident Matrix (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-22T22:53:08+00:00

## By (category, mode)

| Category | Mode | Count |
|---|---|---|
| chat_response_failure | WARM | 1765 |
| health_failure | COLD | 152 |
| chat_failure | COLD | 112 |
| backend_unhealthy | HOT | 13 |
| cycle_crash_recovery | WARM | 9 |
| tab_switch_failure | WARM | 6 |
| chat_send_failure | WARM | 5 |
| chat_failure | HOT | 3 |
| auth_recovery_failure | WARM | 2 |
| auth_bootstrap_failure | WARM | 1 |
| context_recycle_failure | HOT | 1 |
| cookie_failure | COLD | 1 |
| context_recycle_failure | WARM | 1 |

## Product defects (zero target)

- Count: **0**

## Harness defects (fixed during campaign)

- See `docs/runs/campaign_hardening_log.md` for the chronological root-cause narrative. Each harness bug has a commit SHA.

## CI/Workflow incidents

- See `CHANGELOG.md` [Unreleased] block. Tests + WaggleDance CI both green on main since commit `c7f6201` (2026-04-20).
