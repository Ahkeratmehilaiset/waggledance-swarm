# Daily Summary — Day 011 (2026-04-23)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T11:10:34+00:00

## Totals this day

- Green hours: 8.01h
- Cumulative: 135.10h / 400h
- Segments completed: 1
- HOT queries: 2240
- Incidents: 167

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 46 | HOT | 8.01 |

## HOT query latency

- Responded: 1668 / 2240
- Avg latency (responded >100ms): 4205.0 ms
- p95: 14905 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 399 | 355 | 89% |
| ambiguous | 345 | 193 | 56% |
| burst | 168 | 97 | 58% |
| edge_case | 168 | 109 | 65% |
| multilingual | 320 | 203 | 63% |
| normal | 540 | 464 | 86% |
| structured | 300 | 247 | 82% |

## Incidents by category

| category | count |
|---|---|
| chat_response_failure | 152 |
| chat_failure | 8 |
| health_failure | 3 |
| auth_recovery_failure | 2 |
| cycle_crash_recovery | 2 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
