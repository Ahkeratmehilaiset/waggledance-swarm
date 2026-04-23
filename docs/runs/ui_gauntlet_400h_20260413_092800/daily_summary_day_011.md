# Daily Summary — Day 011 (2026-04-23)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T23:05:24+00:00

## Totals this day

- Green hours: 16.01h
- Cumulative: 143.10h / 400h
- Segments completed: 2
- HOT queries: 6487
- Incidents: 208

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 46 | HOT | 8.01 |
| 50 | HOT | 8.00 |

## HOT query latency

- Responded: 5792 / 6487
- Avg latency (responded >100ms): 3301.0 ms
- p95: 6576 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 1053 | 991 | 94% |
| ambiguous | 966 | 814 | 84% |
| burst | 546 | 467 | 86% |
| edge_case | 546 | 391 | 72% |
| multilingual | 838 | 721 | 86% |
| normal | 1698 | 1621 | 95% |
| structured | 840 | 787 | 94% |

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
