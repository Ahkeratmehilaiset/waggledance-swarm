# 400h Campaign — Incident Matrix (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-25T03:10:29+00:00

## By (category, mode)

| Category | Mode | Count |
|---|---|---|
| chat_response_failure | WARM | 1946 |
| health_failure | COLD | 239 |
| chat_failure | COLD | 121 |
| cycle_crash_recovery | WARM | 28 |
| backend_unhealthy | HOT | 23 |
| auth_recovery_failure | WARM | 20 |
| chat_send_failure | WARM | 14 |
| tab_switch_failure | WARM | 6 |
| chat_failure | HOT | 4 |
| cookie_failure | COLD | 4 |
| context_recycle_failure | HOT | 2 |
| context_recycle_failure | WARM | 2 |
| auth_bootstrap_failure | WARM | 1 |

## Product defects (zero target)

- Count: **0**

## Harness defects (fixed during campaign)

- See `docs/runs/campaign_hardening_log.md` for the chronological root-cause narrative. Each harness bug has a commit SHA.

## CI/Workflow incidents

- See `CHANGELOG.md` [Unreleased] block. Tests + WaggleDance CI both green on main since commit `c7f6201` (2026-04-20).
