# 400h Campaign — Final Summary (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T11:40:39+00:00

## Cumulative hours (evidence-backed)

| Mode | Cumulative | Target | % |
|---|---|---|---|
| HOT  | 135.10h  | 80h  | 168.9% |
| WARM | 88.14h | 120h | 73.5% |
| COLD | 20.03h | 200h | 10.0% |
| **TOTAL** | **243.27h** | **400h** | **60.8%** |

## Queries

- Total: 34973
- Sent: 34970
- Responded: 24550
- Skipped empty: 0
- XSS hits: **0** (zero-tolerance target: 0)
- DOM breaks: **0** (zero-tolerance target: 0)
- Session losses: 22293
- Avg latency: 3839.0 ms
- Median latency: 2445.0 ms
- p95 latency: 11665 ms

## Backend truth (from COLD mode)

- Health pass rate: 551/568
- Feeds monotonic: True
- Hologram honest: False
- Auth chat pass rate: 6/21
- Cookie bootstrap pass rate: 41/41

## Incidents classified

| Category | Count |
|---|---|
| chat_response_failure | 1932 |
| health_failure | 157 |
| chat_failure | 124 |
| backend_unhealthy | 13 |
| cycle_crash_recovery | 13 |
| chat_send_failure | 7 |
| auth_recovery_failure | 6 |
| tab_switch_failure | 6 |
| context_recycle_failure | 2 |
| auth_bootstrap_failure | 1 |
| cookie_failure | 1 |

## Known carries (from x.txt Phase 0 intake, not product bugs)

- voikko_dll_missing
- rss_ha_blog_dns
- ollama_embed_timeout_warnings
- github_actions_node20_deprecation

## Verdict per x.txt Phase 7 questions

- Campaign incomplete (243.3h / 400h). Verdict fields will be filled
  by rerunning `python tools/campaign_reports.py aggregate` once total ≥ 400h.
