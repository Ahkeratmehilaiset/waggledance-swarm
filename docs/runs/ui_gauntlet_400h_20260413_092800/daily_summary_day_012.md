# Daily Summary — Day 012 (2026-04-24)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-25T20:42:40+00:00

## Totals this day

- Green hours: 16.00h
- Cumulative: 167.10h / 400h
- Segments completed: 2
- HOT queries: 8503
- Incidents: 96

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 72 | HOT | 8.00 |
| 77 | HOT | 8.00 |

## HOT query latency

- Responded: 8212 / 8503
- Avg latency (responded >100ms): 3013.0 ms
- p95: 3676 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 1458 | 1427 | 98% |
| ambiguous | 1242 | 1242 | 100% |
| burst | 714 | 710 | 99% |
| edge_case | 749 | 493 | 66% |
| multilingual | 1152 | 1152 | 100% |
| normal | 2108 | 2108 | 100% |
| structured | 1080 | 1080 | 100% |

## Incidents by category

| category | count |
|---|---|
| health_failure | 44 |
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
