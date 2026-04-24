# 400h Campaign — Final Summary (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-24T05:06:21+00:00

## Cumulative hours (evidence-backed)

| Mode | Cumulative | Target | % |
|---|---|---|---|
| HOT  | 151.10h  | 80h  | 188.9% |
| WARM | 104.16h | 120h | 86.8% |
| COLD | 32.04h | 200h | 16.0% |
| **TOTAL** | **287.30h** | **400h** | **71.8%** |

## Queries

- Total: 41113
- Sent: 41110
- Responded: 30509
- Skipped empty: 0
- XSS hits: **0** (zero-tolerance target: 0)
- DOM breaks: **0** (zero-tolerance target: 0)
- Session losses: 22585
- Avg latency: 3669.0 ms
- Median latency: 2467 ms
- p95 latency: 10713 ms

## Backend truth (from COLD mode)

- Health pass rate: 865/915
- Feeds monotonic: True
- Hologram honest: False
- Auth chat pass rate: 16/33
- Cookie bootstrap pass rate: 62/65

## Incidents classified

| Category | Count |
|---|---|
| chat_response_failure | 1942 |
| health_failure | 206 |
| chat_failure | 124 |
| backend_unhealthy | 19 |
| cycle_crash_recovery | 16 |
| chat_send_failure | 9 |
| auth_recovery_failure | 9 |
| tab_switch_failure | 6 |
| cookie_failure | 3 |
| context_recycle_failure | 2 |
| auth_bootstrap_failure | 1 |

## Known carries (from x.txt Phase 0 intake, not product bugs)

- voikko_dll_missing
- rss_ha_blog_dns
- ollama_embed_timeout_warnings
- github_actions_node20_deprecation

## Verdict per x.txt Phase 7 questions

- Campaign incomplete (287.3h / 400h). Verdict fields will be filled
  by rerunning `python tools/campaign_reports.py aggregate` once total ≥ 400h.
