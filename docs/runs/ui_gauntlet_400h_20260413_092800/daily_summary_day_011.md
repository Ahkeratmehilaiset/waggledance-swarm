# Daily Summary — Day 011 (2026-04-23)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T21:35:08+00:00

## Totals this day

- Green hours: 16.01h
- Cumulative: 143.10h / 400h
- Segments completed: 2
- HOT queries: 5965
- Incidents: 201

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 46 | HOT | 8.01 |
| 50 | HOT | 8.00 |

## HOT query latency

- Responded: 5285 / 5965
- Avg latency (responded >100ms): 3318.0 ms
- p95: 7575 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 972 | 913 | 94% |
| ambiguous | 897 | 745 | 83% |
| burst | 504 | 427 | 85% |
| edge_case | 504 | 359 | 71% |
| multilingual | 768 | 651 | 85% |
| normal | 1579 | 1502 | 95% |
| structured | 741 | 688 | 93% |

## Incidents by category

| category | count |
|---|---|
| chat_response_failure | 157 |
| health_failure | 25 |
| chat_failure | 8 |
| auth_recovery_failure | 3 |
| cycle_crash_recovery | 3 |
| backend_unhealthy | 3 |
| chat_send_failure | 2 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
