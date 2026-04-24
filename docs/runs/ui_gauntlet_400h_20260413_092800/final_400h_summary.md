# 400h Campaign — Final Summary (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-24T21:38:21+00:00

## Cumulative hours (evidence-backed)

| Mode | Cumulative | Target | % |
|---|---|---|---|
| HOT  | 167.10h  | 80h  | 208.9% |
| WARM | 120.18h | 120h | 100.1% |
| COLD | 48.04h | 200h | 24.0% |
| **TOTAL** | **335.32h** | **400h** | **83.8%** |

## Queries

- Total: 46978
- Sent: 46974
- Responded: 36173
- Skipped empty: 0
- XSS hits: **0** (zero-tolerance target: 0)
- DOM breaks: **0** (zero-tolerance target: 0)
- Session losses: 23327
- Avg latency: 3569.0 ms
- Median latency: 2492 ms
- p95 latency: 9764 ms

## Backend truth (from COLD mode)

- Health pass rate: 1309/1385
- Feeds monotonic: True
- Hologram honest: False
- Auth chat pass rate: 30/49
- Cookie bootstrap pass rate: 92/97

## Incidents classified

| Category | Count |
|---|---|
| chat_response_failure | 1946 |
| health_failure | 233 |
| chat_failure | 125 |
| cycle_crash_recovery | 28 |
| backend_unhealthy | 23 |
| auth_recovery_failure | 20 |
| chat_send_failure | 14 |
| tab_switch_failure | 6 |
| context_recycle_failure | 4 |
| cookie_failure | 4 |
| auth_bootstrap_failure | 1 |

## Known carries (from x.txt Phase 0 intake, not product bugs)

- voikko_dll_missing
- rss_ha_blog_dns
- ollama_embed_timeout_warnings
- github_actions_node20_deprecation

## Verdict per x.txt Phase 7 questions

- Campaign incomplete (335.3h / 400h). Verdict fields will be filled
  by rerunning `python tools/campaign_reports.py aggregate` once total ≥ 400h.
