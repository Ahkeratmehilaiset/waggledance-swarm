# Daily Summary — Day 002 (2026-04-14)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-24T07:36:39+00:00

## Totals this day

- Green hours: 24.12h
- Cumulative: 26.18h / 400h
- Segments completed: 3
- HOT queries: 6442
- Incidents: 363

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 2 | HOT | 8.01 |
| 3 | HOT | 8.11 |
| 6 | HOT | 8.00 |

## HOT query latency

- Responded: 3967 / 6442
- Avg latency (responded >100ms): 3115.0 ms
- p95: 7631 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 1110 | 929 | 84% |
| ambiguous | 943 | 281 | 30% |
| burst | 529 | 92 | 17% |
| edge_case | 562 | 279 | 50% |
| multilingual | 832 | 401 | 48% |
| normal | 1686 | 1386 | 82% |
| structured | 780 | 599 | 77% |

## Incidents by category

| category | count |
|---|---|
| chat_response_failure | 310 |
| health_failure | 30 |
| chat_failure | 22 |
| backend_unhealthy | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
