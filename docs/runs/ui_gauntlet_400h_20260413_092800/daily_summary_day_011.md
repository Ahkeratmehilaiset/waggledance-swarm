# Daily Summary — Day 011 (2026-04-23)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T11:40:39+00:00

## Totals this day

- Green hours: 8.01h
- Cumulative: 135.10h / 400h
- Segments completed: 1
- HOT queries: 2419
- Incidents: 171

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 46 | HOT | 8.01 |

## HOT query latency

- Responded: 1835 / 2419
- Avg latency (responded >100ms): 4082.0 ms
- p95: 13927 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 405 | 360 | 89% |
| ambiguous | 345 | 193 | 56% |
| burst | 210 | 139 | 66% |
| edge_case | 210 | 140 | 67% |
| multilingual | 320 | 203 | 63% |
| normal | 629 | 553 | 88% |
| structured | 300 | 247 | 82% |

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
