# Daily Summary — Day 012 (2026-04-24)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-24T17:37:50+00:00

## Totals this day

- Green hours: 8.00h
- Cumulative: 159.10h / 400h
- Segments completed: 1
- HOT queries: 6249
- Incidents: 80

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 72 | HOT | 8.00 |

## HOT query latency

- Responded: 6039 / 6249
- Avg latency (responded >100ms): 2998.0 ms
- p95: 3670 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 1053 | 1028 | 98% |
| ambiguous | 897 | 897 | 100% |
| burst | 546 | 542 | 99% |
| edge_case | 546 | 365 | 67% |
| multilingual | 832 | 832 | 100% |
| normal | 1595 | 1595 | 100% |
| structured | 780 | 780 | 100% |

## Incidents by category

| category | count |
|---|---|
| health_failure | 35 |
| cycle_crash_recovery | 13 |
| auth_recovery_failure | 12 |
| chat_response_failure | 6 |
| chat_send_failure | 5 |
| backend_unhealthy | 4 |
| cookie_failure | 2 |
| context_recycle_failure | 2 |
| chat_failure | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
