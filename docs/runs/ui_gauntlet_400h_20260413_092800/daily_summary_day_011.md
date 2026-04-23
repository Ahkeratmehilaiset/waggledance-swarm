# Daily Summary — Day 011 (2026-04-23)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T19:34:47+00:00

## Totals this day

- Green hours: 16.01h
- Cumulative: 143.10h / 400h
- Segments completed: 2
- HOT queries: 5260
- Incidents: 193

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 46 | HOT | 8.01 |
| 50 | HOT | 8.00 |

## HOT query latency

- Responded: 4595 / 5260
- Avg latency (responded >100ms): 3343.0 ms
- p95: 8628 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 891 | 834 | 94% |
| ambiguous | 759 | 607 | 80% |
| burst | 443 | 368 | 83% |
| edge_case | 462 | 328 | 71% |
| multilingual | 704 | 587 | 83% |
| normal | 1341 | 1264 | 94% |
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
