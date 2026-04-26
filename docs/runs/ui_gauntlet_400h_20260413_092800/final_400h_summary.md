# 400h Campaign — Final Summary (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-26T07:13:57+00:00

## Cumulative hours (evidence-backed)

| Mode | Cumulative | Target | % |
|---|---|---|---|
| HOT  | 199.11h  | 80h  | 248.9% |
| WARM | 120.18h | 120h | 100.1% |
| COLD | 80.05h | 200h | 40.0% |
| **TOTAL** | **399.33h** | **400h** | **99.8%** |

## Queries

- Total: 58751
- Sent: 58744
- Responded: 47526
- Skipped empty: 0
- XSS hits: **0** (zero-tolerance target: 0)
- DOM breaks: **0** (zero-tolerance target: 0)
- Session losses: 24856
- Avg latency: 3434.0 ms
- Median latency: 2530.0 ms
- p95 latency: 8637 ms

## Backend truth (from COLD mode)

- Health pass rate: 2185/2313
- Feeds monotonic: True
- Hologram honest: False
- Auth chat pass rate: 61/81
- Cookie bootstrap pass rate: 152/161

## Incidents classified

| Category | Count |
|---|---|
| chat_response_failure | 1946 |
| health_failure | 288 |
| chat_failure | 128 |
| cycle_crash_recovery | 28 |
| backend_unhealthy | 27 |
| auth_recovery_failure | 20 |
| chat_send_failure | 14 |
| context_recycle_failure | 7 |
| tab_switch_failure | 6 |
| cookie_failure | 6 |
| auth_bootstrap_failure | 1 |

## Known carries (from x.txt Phase 0 intake, not product bugs)

- voikko_dll_missing
- rss_ha_blog_dns
- ollama_embed_timeout_warnings
- github_actions_node20_deprecation

## Verdict per x.txt Phase 7 questions

- Campaign incomplete (399.3h / 400h). Verdict fields will be filled
  by rerunning `python tools/campaign_reports.py aggregate` once total ≥ 400h.
