# 400h Campaign — Final Summary (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T23:05:24+00:00

## Cumulative hours (evidence-backed)

| Mode | Cumulative | Target | % |
|---|---|---|---|
| HOT  | 143.10h  | 80h  | 178.9% |
| WARM | 96.15h | 120h | 80.1% |
| COLD | 28.03h | 200h | 14.0% |
| **TOTAL** | **267.28h** | **400h** | **66.8%** |

## Queries

- Total: 39041
- Sent: 39038
- Responded: 28507
- Skipped empty: 0
- XSS hits: **0** (zero-tolerance target: 0)
- DOM breaks: **0** (zero-tolerance target: 0)
- Session losses: 22437
- Avg latency: 3714.0 ms
- Median latency: 2458 ms
- p95 latency: 10782 ms

## Backend truth (from COLD mode)

- Health pass rate: 764/798
- Feeds monotonic: True
- Hologram honest: False
- Auth chat pass rate: 12/29
- Cookie bootstrap pass rate: 55/57

## Incidents classified

| Category | Count |
|---|---|
| chat_response_failure | 1939 |
| health_failure | 182 |
| chat_failure | 124 |
| backend_unhealthy | 16 |
| cycle_crash_recovery | 13 |
| chat_send_failure | 8 |
| auth_recovery_failure | 6 |
| tab_switch_failure | 6 |
| context_recycle_failure | 2 |
| cookie_failure | 2 |
| auth_bootstrap_failure | 1 |

## Known carries (from x.txt Phase 0 intake, not product bugs)

- voikko_dll_missing
- rss_ha_blog_dns
- ollama_embed_timeout_warnings
- github_actions_node20_deprecation

## Verdict per x.txt Phase 7 questions

- Campaign incomplete (267.3h / 400h). Verdict fields will be filled
  by rerunning `python tools/campaign_reports.py aggregate` once total ≥ 400h.
