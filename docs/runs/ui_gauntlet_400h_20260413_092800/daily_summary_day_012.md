# Daily Summary — Day 012 (2026-04-24)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-24T16:37:43+00:00

## Totals this day

- Green hours: 8.00h
- Cumulative: 159.10h / 400h
- Segments completed: 1
- HOT queries: 5900
- Incidents: 77

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 72 | HOT | 8.00 |

## HOT query latency

- Responded: 5706 / 5900
- Avg latency (responded >100ms): 3000.0 ms
- p95: 3670 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 972 | 948 | 98% |
| ambiguous | 897 | 897 | 100% |
| burst | 504 | 500 | 99% |
| edge_case | 504 | 338 | 67% |
| multilingual | 768 | 768 | 100% |
| normal | 1513 | 1513 | 100% |
| structured | 742 | 742 | 100% |

## Incidents by category

| category | count |
|---|---|
| health_failure | 33 |
| cycle_crash_recovery | 13 |
| auth_recovery_failure | 12 |
| chat_response_failure | 6 |
| chat_send_failure | 5 |
| backend_unhealthy | 3 |
| cookie_failure | 2 |
| context_recycle_failure | 2 |
| chat_failure | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
