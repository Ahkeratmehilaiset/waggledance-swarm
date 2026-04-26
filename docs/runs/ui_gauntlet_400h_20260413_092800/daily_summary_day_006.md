# Daily Summary — Day 006 (2026-04-18)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-26T13:14:40+00:00

## Totals this day

- Green hours: 19.31h
- Cumulative: 101.84h / 400h
- Segments completed: 2
- HOT queries: 3682
- Incidents: 276

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 30 | HOT | 8.00 |
| 33 | HOT | 11.31 |

## HOT query latency

- Responded: 2716 / 3682
- Avg latency (responded >100ms): 4130.0 ms
- p95: 13801 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 526 | 458 | 87% |
| ambiguous | 552 | 314 | 57% |
| burst | 294 | 122 | 41% |
| edge_case | 294 | 157 | 53% |
| multilingual | 415 | 270 | 65% |
| normal | 1121 | 995 | 89% |
| structured | 480 | 400 | 83% |

## Incidents by category

| category | count |
|---|---|
| chat_response_failure | 242 |
| health_failure | 16 |
| chat_failure | 16 |
| backend_unhealthy | 1 |
| chat_send_failure | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
