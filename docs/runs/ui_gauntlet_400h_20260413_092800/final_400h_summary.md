# 400h Campaign — Final Summary (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T17:34:27+00:00

## Cumulative hours (evidence-backed)

| Mode | Cumulative | Target | % |
|---|---|---|---|
| HOT  | 143.10h  | 80h  | 178.9% |
| WARM | 96.15h | 120h | 80.1% |
| COLD | 24.03h | 200h | 12.0% |
| **TOTAL** | **263.28h** | **400h** | **65.8%** |

## Queries

- Total: 37126
- Sent: 37123
- Responded: 26650
- Skipped empty: 0
- XSS hits: **0** (zero-tolerance target: 0)
- DOM breaks: **0** (zero-tolerance target: 0)
- Session losses: 22336
- Avg latency: 3759.0 ms
- Median latency: 2450.0 ms
- p95 latency: 10887 ms

## Backend truth (from COLD mode)

- Health pass rate: 661/681
- Feeds monotonic: True
- Hologram honest: False
- Auth chat pass rate: 8/25
- Cookie bootstrap pass rate: 49/49

## Incidents classified

| Category | Count |
|---|---|
| chat_response_failure | 1934 |
| health_failure | 165 |
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

- Campaign incomplete (263.3h / 400h). Verdict fields will be filled
  by rerunning `python tools/campaign_reports.py aggregate` once total ≥ 400h.
