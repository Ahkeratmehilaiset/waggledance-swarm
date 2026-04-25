# Daily Summary — Day 004 (2026-04-16)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-25T07:11:02+00:00

## Totals this day

- Green hours: 24.24h
- Cumulative: 74.42h / 400h
- Segments completed: 3
- HOT queries: 4109
- Incidents: 334

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 18 | HOT | 8.00 |
| 21 | HOT | 8.12 |
| 24 | HOT | 8.11 |

## HOT query latency

- Responded: 2807 / 4109
- Avg latency (responded >100ms): 3636.0 ms
- p95: 10746 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 729 | 628 | 86% |
| ambiguous | 581 | 264 | 45% |
| burst | 323 | 113 | 35% |
| edge_case | 357 | 200 | 56% |
| multilingual | 576 | 338 | 59% |
| normal | 1003 | 838 | 84% |
| structured | 540 | 426 | 79% |

## Incidents by category

| category | count |
|---|---|
| chat_response_failure | 296 |
| health_failure | 21 |
| chat_failure | 13 |
| backend_unhealthy | 3 |
| chat_send_failure | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
