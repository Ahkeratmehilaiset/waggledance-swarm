# 400h Campaign — Final Summary (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-24T14:07:26+00:00

## Cumulative hours (evidence-backed)

| Mode | Cumulative | Target | % |
|---|---|---|---|
| HOT  | 159.10h  | 80h  | 198.9% |
| WARM | 112.17h | 120h | 93.5% |
| COLD | 40.04h | 200h | 20.0% |
| **TOTAL** | **311.31h** | **400h** | **77.8%** |

## Queries

- Total: 44333
- Sent: 44329
- Responded: 33626
- Skipped empty: 0
- XSS hits: **0** (zero-tolerance target: 0)
- DOM breaks: **0** (zero-tolerance target: 0)
- Session losses: 22982
- Avg latency: 3609.0 ms
- Median latency: 2480.0 ms
- p95 latency: 9907 ms

## Backend truth (from COLD mode)

- Health pass rate: 1087/1151
- Feeds monotonic: True
- Hologram honest: False
- Auth chat pass rate: 23/41
- Cookie bootstrap pass rate: 77/81

## Incidents classified

| Category | Count |
|---|---|
| chat_response_failure | 1944 |
| health_failure | 221 |
| chat_failure | 125 |
| cycle_crash_recovery | 24 |
| backend_unhealthy | 21 |
| auth_recovery_failure | 16 |
| chat_send_failure | 13 |
| tab_switch_failure | 6 |
| context_recycle_failure | 4 |
| cookie_failure | 3 |
| auth_bootstrap_failure | 1 |

## Known carries (from x.txt Phase 0 intake, not product bugs)

- voikko_dll_missing
- rss_ha_blog_dns
- ollama_embed_timeout_warnings
- github_actions_node20_deprecation

## Verdict per x.txt Phase 7 questions

- Campaign incomplete (311.3h / 400h). Verdict fields will be filled
  by rerunning `python tools/campaign_reports.py aggregate` once total ≥ 400h.
