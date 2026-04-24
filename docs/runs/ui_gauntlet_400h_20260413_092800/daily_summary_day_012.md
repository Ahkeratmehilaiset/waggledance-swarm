# Daily Summary — Day 012 (2026-04-24)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-24T20:08:09+00:00

## Totals this day

- Green hours: 8.00h
- Cumulative: 159.10h / 400h
- Segments completed: 1
- HOT queries: 7126
- Incidents: 91

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 72 | HOT | 8.00 |

## HOT query latency

- Responded: 6885 / 7126
- Avg latency (responded >100ms): 3008.0 ms
- p95: 3671 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 1215 | 1187 | 98% |
| ambiguous | 1035 | 1035 | 100% |
| burst | 630 | 626 | 99% |
| edge_case | 630 | 421 | 67% |
| multilingual | 960 | 960 | 100% |
| normal | 1756 | 1756 | 100% |
| structured | 900 | 900 | 100% |

## Incidents by category

| category | count |
|---|---|
| health_failure | 39 |
| cycle_crash_recovery | 15 |
| auth_recovery_failure | 14 |
| chat_response_failure | 7 |
| chat_send_failure | 6 |
| backend_unhealthy | 5 |
| cookie_failure | 2 |
| context_recycle_failure | 2 |
| chat_failure | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
