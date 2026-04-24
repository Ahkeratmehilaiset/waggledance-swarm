# Daily Summary — Day 012 (2026-04-24)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-24T13:37:22+00:00

## Totals this day

- Green hours: 8.00h
- Cumulative: 159.10h / 400h
- Segments completed: 1
- HOT queries: 4832
- Incidents: 66

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 72 | HOT | 8.00 |

## HOT query latency

- Responded: 4671 / 4832
- Avg latency (responded >100ms): 2987.0 ms
- p95: 3668 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 810 | 789 | 97% |
| ambiguous | 690 | 690 | 100% |
| burst | 420 | 416 | 99% |
| edge_case | 420 | 284 | 68% |
| multilingual | 640 | 640 | 100% |
| normal | 1252 | 1252 | 100% |
| structured | 600 | 600 | 100% |

## Incidents by category

| category | count |
|---|---|
| health_failure | 28 |
| cycle_crash_recovery | 11 |
| auth_recovery_failure | 10 |
| chat_response_failure | 5 |
| chat_send_failure | 5 |
| backend_unhealthy | 3 |
| context_recycle_failure | 2 |
| cookie_failure | 1 |
| chat_failure | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
