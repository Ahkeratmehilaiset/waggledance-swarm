# Daily Summary — Day 012 (2026-04-24)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-24T18:37:57+00:00

## Totals this day

- Green hours: 8.00h
- Cumulative: 159.10h / 400h
- Segments completed: 1
- HOT queries: 6595
- Incidents: 84

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 72 | HOT | 8.00 |

## HOT query latency

- Responded: 6370 / 6595
- Avg latency (responded >100ms): 3004.0 ms
- p95: 3670 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 1134 | 1107 | 98% |
| ambiguous | 966 | 966 | 100% |
| burst | 546 | 542 | 99% |
| edge_case | 581 | 387 | 67% |
| multilingual | 896 | 896 | 100% |
| normal | 1632 | 1632 | 100% |
| structured | 840 | 840 | 100% |

## Incidents by category

| category | count |
|---|---|
| health_failure | 36 |
| cycle_crash_recovery | 14 |
| auth_recovery_failure | 13 |
| chat_response_failure | 6 |
| chat_send_failure | 6 |
| backend_unhealthy | 4 |
| cookie_failure | 2 |
| context_recycle_failure | 2 |
| chat_failure | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
