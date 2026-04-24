# Daily Summary — Day 012 (2026-04-24)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-24T19:38:05+00:00

## Totals this day

- Green hours: 8.00h
- Cumulative: 159.10h / 400h
- Segments completed: 1
- HOT queries: 6974
- Incidents: 87

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 72 | HOT | 8.00 |

## HOT query latency

- Responded: 6749 / 6974
- Avg latency (responded >100ms): 3007.0 ms
- p95: 3671 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 1152 | 1125 | 98% |
| ambiguous | 1035 | 1035 | 100% |
| burst | 588 | 584 | 99% |
| edge_case | 588 | 394 | 67% |
| multilingual | 960 | 960 | 100% |
| normal | 1751 | 1751 | 100% |
| structured | 900 | 900 | 100% |

## Incidents by category

| category | count |
|---|---|
| health_failure | 37 |
| cycle_crash_recovery | 15 |
| auth_recovery_failure | 14 |
| chat_response_failure | 6 |
| chat_send_failure | 6 |
| backend_unhealthy | 4 |
| cookie_failure | 2 |
| context_recycle_failure | 2 |
| chat_failure | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
