# Daily Summary — Day 012 (2026-04-24)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-24T12:07:10+00:00

## Totals this day

- Green hours: 8.00h
- Cumulative: 159.10h / 400h
- Segments completed: 1
- HOT queries: 4296
- Incidents: 62

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 72 | HOT | 8.00 |

## HOT query latency

- Responded: 4154 / 4296
- Avg latency (responded >100ms): 2987.0 ms
- p95: 3667 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 729 | 710 | 97% |
| ambiguous | 621 | 621 | 100% |
| burst | 353 | 349 | 99% |
| edge_case | 378 | 259 | 69% |
| multilingual | 576 | 576 | 100% |
| normal | 1099 | 1099 | 100% |
| structured | 540 | 540 | 100% |

## Incidents by category

| category | count |
|---|---|
| health_failure | 26 |
| cycle_crash_recovery | 10 |
| auth_recovery_failure | 9 |
| chat_response_failure | 5 |
| chat_send_failure | 5 |
| backend_unhealthy | 3 |
| context_recycle_failure | 2 |
| cookie_failure | 1 |
| chat_failure | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
