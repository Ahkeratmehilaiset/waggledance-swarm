# 400h Campaign — Final Summary (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-22T19:10:25+00:00

## Cumulative hours (evidence-backed)

| Mode | Cumulative | Target | % |
|---|---|---|---|
| HOT  | 119.09h  | 80h  | 148.9% |
| WARM | 72.11h | 120h | 60.1% |
| COLD | 4.02h | 200h | 2.0% |
| **TOTAL** | **195.22h** | **400h** | **48.8%** |

## Queries

- Total: 31383
- Sent: 31380
- Responded: 21735
- Skipped empty: 0
- XSS hits: **0** (zero-tolerance target: 0)
- DOM breaks: **0** (zero-tolerance target: 0)
- Session losses: 20314
- Avg latency: 3795.0 ms
- Median latency: 2447 ms
- p95 latency: 10897 ms

## Backend truth (from COLD mode)

- Health pass rate: 110/114
- Feeds monotonic: True
- Hologram honest: False
- Auth chat pass rate: 1/5
- Cookie bootstrap pass rate: 9/9

## Incidents classified

| Category | Count |
|---|---|
| chat_response_failure | 1742 |
| health_failure | 148 |
| chat_failure | 112 |
| backend_unhealthy | 13 |
| cycle_crash_recovery | 8 |
| tab_switch_failure | 6 |
| chat_send_failure | 4 |
| context_recycle_failure | 2 |
| auth_bootstrap_failure | 1 |
| auth_recovery_failure | 1 |
| cookie_failure | 1 |

## Known carries (from x.txt Phase 0 intake, not product bugs)

- voikko_dll_missing
- rss_ha_blog_dns
- ollama_embed_timeout_warnings
- github_actions_node20_deprecation

## Verdict per x.txt Phase 7 questions

- Campaign incomplete (195.2h / 400h). Verdict fields will be filled
  by rerunning `python tools/campaign_reports.py aggregate` once total ≥ 400h.
