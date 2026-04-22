# 400h Campaign — Final Summary (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-22T23:03:36+00:00

## Cumulative hours (evidence-backed)

| Mode | Cumulative | Target | % |
|---|---|---|---|
| HOT  | 127.09h  | 80h  | 158.9% |
| WARM | 80.12h | 120h | 66.8% |
| COLD | 8.03h | 200h | 4.0% |
| **TOTAL** | **215.24h** | **400h** | **53.8%** |

## Queries

- Total: 32383
- Sent: 32380
- Responded: 22597
- Skipped empty: 0
- XSS hits: **0** (zero-tolerance target: 0)
- DOM breaks: **0** (zero-tolerance target: 0)
- Session losses: 20596
- Avg latency: 3818.0 ms
- Median latency: 2447 ms
- p95 latency: 10954 ms

## Backend truth (from COLD mode)

- Health pass rate: 219/229
- Feeds monotonic: True
- Hologram honest: False
- Auth chat pass rate: 3/9
- Cookie bootstrap pass rate: 17/17

## Incidents classified

| Category | Count |
|---|---|
| chat_response_failure | 1765 |
| health_failure | 152 |
| chat_failure | 115 |
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

- Campaign incomplete (215.2h / 400h). Verdict fields will be filled
  by rerunning `python tools/campaign_reports.py aggregate` once total ≥ 400h.
