# 400h Campaign — Final Summary (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-24T08:36:46+00:00

## Cumulative hours (evidence-backed)

| Mode | Cumulative | Target | % |
|---|---|---|---|
| HOT  | 151.10h  | 80h  | 188.9% |
| WARM | 104.16h | 120h | 86.8% |
| COLD | 36.04h | 200h | 18.0% |
| **TOTAL** | **291.30h** | **400h** | **72.8%** |

## Queries

- Total: 42367
- Sent: 42364
- Responded: 31727
- Skipped empty: 0
- XSS hits: **0** (zero-tolerance target: 0)
- DOM breaks: **0** (zero-tolerance target: 0)
- Session losses: 22773
- Avg latency: 3645.0 ms
- Median latency: 2472 ms
- p95 latency: 10671 ms

## Backend truth (from COLD mode)

- Health pass rate: 975/1033
- Feeds monotonic: True
- Hologram honest: False
- Auth chat pass rate: 19/37
- Cookie bootstrap pass rate: 69/73

## Incidents classified

| Category | Count |
|---|---|
| chat_response_failure | 1943 |
| health_failure | 213 |
| chat_failure | 124 |
| cycle_crash_recovery | 20 |
| backend_unhealthy | 19 |
| chat_send_failure | 12 |
| auth_recovery_failure | 12 |
| tab_switch_failure | 6 |
| context_recycle_failure | 3 |
| cookie_failure | 3 |
| auth_bootstrap_failure | 1 |

## Known carries (from x.txt Phase 0 intake, not product bugs)

- voikko_dll_missing
- rss_ha_blog_dns
- ollama_embed_timeout_warnings
- github_actions_node20_deprecation

## Verdict per x.txt Phase 7 questions

- Campaign incomplete (291.3h / 400h). Verdict fields will be filled
  by rerunning `python tools/campaign_reports.py aggregate` once total ≥ 400h.
