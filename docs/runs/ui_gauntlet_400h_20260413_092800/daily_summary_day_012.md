# Daily Summary — Day 012 (2026-04-24)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-24T14:37:29+00:00

## Totals this day

- Green hours: 8.00h
- Cumulative: 159.10h / 400h
- Segments completed: 1
- HOT queries: 5169
- Incidents: 69

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 72 | HOT | 8.00 |

## HOT query latency

- Responded: 4989 / 5169
- Avg latency (responded >100ms): 2993.0 ms
- p95: 3670 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 891 | 868 | 97% |
| ambiguous | 759 | 759 | 100% |
| burst | 420 | 416 | 99% |
| edge_case | 460 | 307 | 67% |
| multilingual | 704 | 704 | 100% |
| normal | 1275 | 1275 | 100% |
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
