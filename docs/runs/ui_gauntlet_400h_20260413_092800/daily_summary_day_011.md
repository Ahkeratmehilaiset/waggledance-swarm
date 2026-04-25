# Daily Summary — Day 011 (2026-04-23)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-25T15:12:01+00:00

## Totals this day

- Green hours: 24.01h
- Cumulative: 151.10h / 400h
- Segments completed: 3
- HOT queries: 6760
- Incidents: 220

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 46 | HOT | 8.01 |
| 50 | HOT | 8.00 |
| 59 | HOT | 8.00 |

## HOT query latency

- Responded: 6053 / 6760
- Avg latency (responded >100ms): 3314.0 ms
- p95: 6668 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 1134 | 1070 | 94% |
| ambiguous | 966 | 814 | 84% |
| burst | 588 | 509 | 87% |
| edge_case | 588 | 423 | 72% |
| multilingual | 896 | 779 | 87% |
| normal | 1748 | 1671 | 96% |
| structured | 840 | 787 | 94% |

## Incidents by category

| category | count |
|---|---|
| chat_response_failure | 159 |
| health_failure | 39 |
| chat_failure | 8 |
| backend_unhealthy | 5 |
| auth_recovery_failure | 3 |
| cycle_crash_recovery | 3 |
| chat_send_failure | 2 |
| cookie_failure | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
