# Daily Summary — Day 011 (2026-04-23)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T20:34:58+00:00

## Totals this day

- Green hours: 16.01h
- Cumulative: 143.10h / 400h
- Segments completed: 2
- HOT queries: 5636
- Incidents: 194

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 46 | HOT | 8.01 |
| 50 | HOT | 8.00 |

## HOT query latency

- Responded: 4971 / 5636
- Avg latency (responded >100ms): 3327.0 ms
- p95: 7660 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 936 | 879 | 94% |
| ambiguous | 828 | 676 | 82% |
| burst | 462 | 387 | 84% |
| edge_case | 462 | 328 | 71% |
| multilingual | 768 | 651 | 85% |
| normal | 1460 | 1383 | 95% |
| structured | 720 | 667 | 93% |

## Incidents by category

| category | count |
|---|---|
| chat_response_failure | 156 |
| health_failure | 21 |
| chat_failure | 8 |
| auth_recovery_failure | 3 |
| cycle_crash_recovery | 3 |
| chat_send_failure | 2 |
| backend_unhealthy | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
