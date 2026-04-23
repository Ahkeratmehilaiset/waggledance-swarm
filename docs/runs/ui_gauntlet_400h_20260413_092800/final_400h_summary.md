# 400h Campaign — Final Summary (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T06:07:17+00:00

## Cumulative hours (evidence-backed)

| Mode | Cumulative | Target | % |
|---|---|---|---|
| HOT  | 135.10h  | 80h  | 168.9% |
| WARM | 88.14h | 120h | 73.5% |
| COLD | 16.03h | 200h | 8.0% |
| **TOTAL** | **239.26h** | **400h** | **59.8%** |

## Queries

- Total: 33660
- Sent: 33657
- Responded: 23466
- Skipped empty: 0
- XSS hits: **0** (zero-tolerance target: 0)
- DOM breaks: **0** (zero-tolerance target: 0)
- Session losses: 21630
- Avg latency: 3831.0 ms
- Median latency: 2445.0 ms
- p95 latency: 11072 ms

## Backend truth (from COLD mode)

- Health pass rate: 438/455
- Feeds monotonic: True
- Hologram honest: False
- Auth chat pass rate: 5/17
- Cookie bootstrap pass rate: 33/33

## Incidents classified

| Category | Count |
|---|---|
| chat_response_failure | 1873 |
| health_failure | 155 |
| chat_failure | 121 |
| backend_unhealthy | 13 |
| cycle_crash_recovery | 10 |
| chat_send_failure | 6 |
| tab_switch_failure | 6 |
| auth_recovery_failure | 3 |
| context_recycle_failure | 2 |
| auth_bootstrap_failure | 1 |
| cookie_failure | 1 |

## Known carries (from x.txt Phase 0 intake, not product bugs)

- voikko_dll_missing
- rss_ha_blog_dns
- ollama_embed_timeout_warnings
- github_actions_node20_deprecation

## Verdict per x.txt Phase 7 questions

- Campaign incomplete (239.3h / 400h). Verdict fields will be filled
  by rerunning `python tools/campaign_reports.py aggregate` once total ≥ 400h.
