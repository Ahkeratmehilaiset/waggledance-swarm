# Daily Summary — Day 011 (2026-04-23)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T21:05:03+00:00

## Totals this day

- Green hours: 16.01h
- Cumulative: 143.10h / 400h
- Segments completed: 2
- HOT queries: 5774
- Incidents: 201

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 46 | HOT | 8.01 |
| 50 | HOT | 8.00 |

## HOT query latency

- Responded: 5094 / 5774
- Avg latency (responded >100ms): 3326.0 ms
- p95: 7644 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 972 | 913 | 94% |
| ambiguous | 828 | 676 | 82% |
| burst | 504 | 427 | 85% |
| edge_case | 504 | 359 | 71% |
| multilingual | 768 | 651 | 85% |
| normal | 1478 | 1401 | 95% |
| structured | 720 | 667 | 93% |

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
