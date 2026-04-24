# Daily Summary — Day 012 (2026-04-24)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-24T21:08:17+00:00

## Totals this day

- Green hours: 16.00h
- Cumulative: 167.10h / 400h
- Segments completed: 2
- HOT queries: 7512
- Incidents: 91

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 72 | HOT | 8.00 |
| 77 | HOT | 8.00 |

## HOT query latency

- Responded: 7271 / 7512
- Avg latency (responded >100ms): 3010.0 ms
- p95: 3673 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 1282 | 1254 | 98% |
| ambiguous | 1104 | 1104 | 100% |
| burst | 630 | 626 | 99% |
| edge_case | 630 | 421 | 67% |
| multilingual | 1024 | 1024 | 100% |
| normal | 1882 | 1882 | 100% |
| structured | 960 | 960 | 100% |

## Incidents by category

| category | count |
|---|---|
| health_failure | 39 |
| cycle_crash_recovery | 15 |
| auth_recovery_failure | 14 |
| chat_response_failure | 7 |
| chat_send_failure | 6 |
| backend_unhealthy | 5 |
| cookie_failure | 2 |
| context_recycle_failure | 2 |
| chat_failure | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
