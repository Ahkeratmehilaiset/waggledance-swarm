# Daily Summary — Day 011 (2026-04-23)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T20:04:52+00:00

## Totals this day

- Green hours: 16.01h
- Cumulative: 143.10h / 400h
- Segments completed: 2
- HOT queries: 5448
- Incidents: 193

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 46 | HOT | 8.01 |
| 50 | HOT | 8.00 |

## HOT query latency

- Responded: 4783 / 5448
- Avg latency (responded >100ms): 3335.0 ms
- p95: 7802 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 891 | 834 | 94% |
| ambiguous | 809 | 657 | 81% |
| burst | 462 | 387 | 84% |
| edge_case | 462 | 328 | 71% |
| multilingual | 704 | 587 | 83% |
| normal | 1460 | 1383 | 95% |
| structured | 660 | 607 | 92% |

## Incidents by category

| category | count |
|---|---|
| chat_response_failure | 156 |
| health_failure | 20 |
| chat_failure | 8 |
| auth_recovery_failure | 3 |
| cycle_crash_recovery | 3 |
| chat_send_failure | 2 |
| backend_unhealthy | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
