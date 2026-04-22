# Daily Summary — Day 010 (2026-04-22)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-22T22:53:07+00:00

## Totals this day

- Green hours: 8.00h
- Cumulative: 127.09h / 400h
- Segments completed: 1
- HOT queries: 2498
- Incidents: 118

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 42 | HOT | 8.00 |

## HOT query latency

- Responded: 2106 / 2498
- Avg latency (responded >100ms): 4439.0 ms
- p95: 14866 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 405 | 376 | 93% |
| ambiguous | 347 | 242 | 70% |
| burst | 210 | 150 | 71% |
| edge_case | 210 | 142 | 68% |
| multilingual | 371 | 318 | 86% |
| normal | 595 | 552 | 93% |
| structured | 360 | 326 | 91% |

## Incidents by category

| category | count |
|---|---|
| chat_response_failure | 83 |
| health_failure | 14 |
| chat_failure | 7 |
| cycle_crash_recovery | 5 |
| backend_unhealthy | 3 |
| tab_switch_failure | 3 |
| context_recycle_failure | 1 |
| auth_recovery_failure | 1 |
| chat_send_failure | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
