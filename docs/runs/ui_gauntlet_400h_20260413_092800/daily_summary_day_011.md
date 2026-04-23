# Daily Summary — Day 011 (2026-04-23)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T12:10:44+00:00

## Totals this day

- Green hours: 8.01h
- Cumulative: 135.10h / 400h
- Segments completed: 1
- HOT queries: 2624
- Incidents: 171

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 46 | HOT | 8.01 |

## HOT query latency

- Responded: 2040 / 2624
- Avg latency (responded >100ms): 3984.0 ms
- p95: 13876 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 405 | 360 | 89% |
| ambiguous | 414 | 262 | 63% |
| burst | 210 | 139 | 66% |
| edge_case | 210 | 140 | 67% |
| multilingual | 366 | 249 | 68% |
| normal | 659 | 583 | 88% |
| structured | 360 | 307 | 85% |

## Incidents by category

| category | count |
|---|---|
| chat_response_failure | 152 |
| chat_failure | 8 |
| health_failure | 4 |
| auth_recovery_failure | 3 |
| cycle_crash_recovery | 3 |
| chat_send_failure | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
