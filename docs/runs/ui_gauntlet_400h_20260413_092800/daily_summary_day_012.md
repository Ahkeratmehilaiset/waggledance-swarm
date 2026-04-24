# Daily Summary — Day 012 (2026-04-24)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-24T22:38:28+00:00

## Totals this day

- Green hours: 16.00h
- Cumulative: 167.10h / 400h
- Segments completed: 2
- HOT queries: 8018
- Incidents: 94

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 72 | HOT | 8.00 |
| 77 | HOT | 8.00 |

## HOT query latency

- Responded: 7745 / 8018
- Avg latency (responded >100ms): 3011.0 ms
- p95: 3675 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 1377 | 1347 | 98% |
| ambiguous | 1173 | 1173 | 100% |
| burst | 672 | 668 | 99% |
| edge_case | 699 | 460 | 66% |
| multilingual | 1088 | 1088 | 100% |
| normal | 1989 | 1989 | 100% |
| structured | 1020 | 1020 | 100% |

## Incidents by category

| category | count |
|---|---|
| health_failure | 42 |
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
