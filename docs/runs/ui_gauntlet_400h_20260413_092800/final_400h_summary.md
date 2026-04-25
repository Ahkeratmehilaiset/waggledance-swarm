# 400h Campaign — Final Summary (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-25T12:41:42+00:00

## Cumulative hours (evidence-backed)

| Mode | Cumulative | Target | % |
|---|---|---|---|
| HOT  | 183.10h  | 80h  | 228.9% |
| WARM | 120.18h | 120h | 100.1% |
| COLD | 64.05h | 200h | 32.0% |
| **TOTAL** | **367.33h** | **400h** | **91.8%** |

## Queries

- Total: 52168
- Sent: 52164
- Responded: 41172
- Skipped empty: 0
- XSS hits: **0** (zero-tolerance target: 0)
- DOM breaks: **0** (zero-tolerance target: 0)
- Session losses: 24034
- Avg latency: 3496.0 ms
- Median latency: 2512.0 ms
- p95 latency: 9299 ms

## Backend truth (from COLD mode)

- Health pass rate: 1742/1842
- Feeds monotonic: True
- Hologram honest: False
- Auth chat pass rate: 45/65
- Cookie bootstrap pass rate: 121/129

## Incidents classified

| Category | Count |
|---|---|
| chat_response_failure | 1946 |
| health_failure | 254 |
| chat_failure | 125 |
| cycle_crash_recovery | 28 |
| backend_unhealthy | 23 |
| auth_recovery_failure | 20 |
| chat_send_failure | 14 |
| tab_switch_failure | 6 |
| cookie_failure | 5 |
| context_recycle_failure | 4 |
| auth_bootstrap_failure | 1 |

## Known carries (from x.txt Phase 0 intake, not product bugs)

- voikko_dll_missing
- rss_ha_blog_dns
- ollama_embed_timeout_warnings
- github_actions_node20_deprecation

## Verdict per x.txt Phase 7 questions

- Campaign incomplete (367.3h / 400h). Verdict fields will be filled
  by rerunning `python tools/campaign_reports.py aggregate` once total ≥ 400h.
