# Daily Summary — Day 012 (2026-04-24)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-24T15:07:33+00:00

## Totals this day

- Green hours: 8.00h
- Cumulative: 159.10h / 400h
- Segments completed: 1
- HOT queries: 5361
- Incidents: 69

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 72 | HOT | 8.00 |

## HOT query latency

- Responded: 5181 / 5361
- Avg latency (responded >100ms): 2995.0 ms
- p95: 3669 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 891 | 868 | 97% |
| ambiguous | 788 | 788 | 100% |
| burst | 462 | 458 | 99% |
| edge_case | 462 | 309 | 67% |
| multilingual | 704 | 704 | 100% |
| normal | 1394 | 1394 | 100% |
| structured | 660 | 660 | 100% |

## Incidents by category

| category | count |
|---|---|
| health_failure | 31 |
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
