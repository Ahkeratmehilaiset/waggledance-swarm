# Daily Summary — Day 011 (2026-04-23)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T22:35:19+00:00

## Totals this day

- Green hours: 16.01h
- Cumulative: 143.10h / 400h
- Segments completed: 2
- HOT queries: 6298
- Incidents: 208

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 46 | HOT | 8.01 |
| 50 | HOT | 8.00 |

## HOT query latency

- Responded: 5603 / 6298
- Avg latency (responded >100ms): 3307.0 ms
- p95: 6668 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 1053 | 991 | 94% |
| ambiguous | 897 | 745 | 83% |
| burst | 546 | 467 | 86% |
| edge_case | 546 | 391 | 72% |
| multilingual | 832 | 715 | 86% |
| normal | 1644 | 1567 | 95% |
| structured | 780 | 727 | 93% |

## Incidents by category

| category | count |
|---|---|
| chat_response_failure | 159 |
| health_failure | 29 |
| chat_failure | 8 |
| auth_recovery_failure | 3 |
| cycle_crash_recovery | 3 |
| backend_unhealthy | 3 |
| chat_send_failure | 2 |
| cookie_failure | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
