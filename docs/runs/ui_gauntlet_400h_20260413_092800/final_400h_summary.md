# 400h Campaign — Final Summary (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-24T18:37:58+00:00

## Cumulative hours (evidence-backed)

| Mode | Cumulative | Target | % |
|---|---|---|---|
| HOT  | 159.10h  | 80h  | 198.9% |
| WARM | 112.17h | 120h | 93.5% |
| COLD | 44.04h | 200h | 22.0% |
| **TOTAL** | **315.31h** | **400h** | **78.8%** |

## Queries

- Total: 45909
- Sent: 45905
- Responded: 35138
- Skipped empty: 0
- XSS hits: **0** (zero-tolerance target: 0)
- DOM breaks: **0** (zero-tolerance target: 0)
- Session losses: 23193
- Avg latency: 3584.0 ms
- Median latency: 2487.0 ms
- p95 latency: 9819 ms

## Backend truth (from COLD mode)

- Health pass rate: 1198/1268
- Feeds monotonic: True
- Hologram honest: False
- Auth chat pass rate: 27/45
- Cookie bootstrap pass rate: 85/89

## Incidents classified

| Category | Count |
|---|---|
| chat_response_failure | 1945 |
| health_failure | 228 |
| chat_failure | 125 |
| cycle_crash_recovery | 27 |
| backend_unhealthy | 22 |
| auth_recovery_failure | 19 |
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

- Campaign incomplete (315.3h / 400h). Verdict fields will be filled
  by rerunning `python tools/campaign_reports.py aggregate` once total ≥ 400h.
