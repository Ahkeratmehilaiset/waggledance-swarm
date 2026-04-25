# 400h Campaign — Final Summary (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-25T17:42:19+00:00

## Cumulative hours (evidence-backed)

| Mode | Cumulative | Target | % |
|---|---|---|---|
| HOT  | 183.10h  | 80h  | 228.9% |
| WARM | 120.18h | 120h | 100.1% |
| COLD | 68.05h | 200h | 34.0% |
| **TOTAL** | **371.33h** | **400h** | **92.8%** |

## Queries

- Total: 53967
- Sent: 53963
- Responded: 42914
- Skipped empty: 0
- XSS hits: **0** (zero-tolerance target: 0)
- DOM breaks: **0** (zero-tolerance target: 0)
- Session losses: 24305
- Avg latency: 3477.0 ms
- Median latency: 2518.0 ms
- p95 latency: 8820 ms

## Backend truth (from COLD mode)

- Health pass rate: 1853/1960
- Feeds monotonic: True
- Hologram honest: False
- Auth chat pass rate: 49/69
- Cookie bootstrap pass rate: 129/137

## Incidents classified

| Category | Count |
|---|---|
| chat_response_failure | 1946 |
| health_failure | 262 |
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

- Campaign incomplete (371.3h / 400h). Verdict fields will be filled
  by rerunning `python tools/campaign_reports.py aggregate` once total ≥ 400h.
