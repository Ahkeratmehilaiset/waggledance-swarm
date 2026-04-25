# 400h Campaign — Final Summary (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-25T21:12:44+00:00

## Cumulative hours (evidence-backed)

| Mode | Cumulative | Target | % |
|---|---|---|---|
| HOT  | 191.11h  | 80h  | 238.9% |
| WARM | 120.18h | 120h | 100.1% |
| COLD | 72.05h | 200h | 36.0% |
| **TOTAL** | **383.33h** | **400h** | **95.8%** |

## Queries

- Total: 55194
- Sent: 55189
- Responded: 44089
- Skipped empty: 0
- XSS hits: **0** (zero-tolerance target: 0)
- DOM breaks: **0** (zero-tolerance target: 0)
- Session losses: 24478
- Avg latency: 3465.0 ms
- Median latency: 2521 ms
- p95 latency: 8741 ms

## Backend truth (from COLD mode)

- Health pass rate: 1964/2077
- Feeds monotonic: True
- Hologram honest: False
- Auth chat pass rate: 53/73
- Cookie bootstrap pass rate: 136/145

## Incidents classified

| Category | Count |
|---|---|
| chat_response_failure | 1946 |
| health_failure | 269 |
| chat_failure | 126 |
| cycle_crash_recovery | 28 |
| backend_unhealthy | 24 |
| auth_recovery_failure | 20 |
| chat_send_failure | 14 |
| tab_switch_failure | 6 |
| cookie_failure | 6 |
| context_recycle_failure | 5 |
| auth_bootstrap_failure | 1 |

## Known carries (from x.txt Phase 0 intake, not product bugs)

- voikko_dll_missing
- rss_ha_blog_dns
- ollama_embed_timeout_warnings
- github_actions_node20_deprecation

## Verdict per x.txt Phase 7 questions

- Campaign incomplete (383.3h / 400h). Verdict fields will be filled
  by rerunning `python tools/campaign_reports.py aggregate` once total ≥ 400h.
