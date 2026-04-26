# 400h Campaign — Final Summary (FINAL)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-26T10:14:19+00:00

## Cumulative hours (evidence-backed)

| Mode | Cumulative | Target | % |
|---|---|---|---|
| HOT  | 199.11h  | 80h  | 248.9% |
| WARM | 120.18h | 120h | 100.1% |
| COLD | 84.05h | 200h | 42.0% |
| **TOTAL** | **403.33h** | **400h** | **100.8%** |

## Queries

- Total: 59787
- Sent: 59780
- Responded: 48510
- Skipped empty: 0
- XSS hits: **0** (zero-tolerance target: 0)
- DOM breaks: **0** (zero-tolerance target: 0)
- Session losses: 25039
- Avg latency: 3425.0 ms
- Median latency: 2533.0 ms
- p95 latency: 8607 ms

## Backend truth (from COLD mode)

- Health pass rate: 2295/2431
- Feeds monotonic: True
- Hologram honest: False
- Auth chat pass rate: 65/85
- Cookie bootstrap pass rate: 160/169

## Incidents classified

| Category | Count |
|---|---|
| chat_response_failure | 1946 |
| health_failure | 294 |
| chat_failure | 128 |
| cycle_crash_recovery | 28 |
| backend_unhealthy | 27 |
| auth_recovery_failure | 20 |
| chat_send_failure | 14 |
| context_recycle_failure | 7 |
| cookie_failure | 7 |
| tab_switch_failure | 6 |
| auth_bootstrap_failure | 1 |

## Known carries (from x.txt Phase 0 intake, not product bugs)

- voikko_dll_missing
- rss_ha_blog_dns
- ollama_embed_timeout_warnings
- github_actions_node20_deprecation

## Verdict per x.txt Phase 7 questions

- **What definitely works:** TBD — to fill at campaign completion from segment analysis.
- **What is fragile:** TBD — to fill from incident correlation.
- **What actually breaks:** TBD — from any PRODUCT-category incidents.
- **First clear break point:** TBD or 'not reached' if no product defect.
