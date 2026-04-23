# Daily Summary — Day 011 (2026-04-23)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T17:34:26+00:00

## Totals this day

- Green hours: 16.01h
- Cumulative: 143.10h / 400h
- Segments completed: 2
- HOT queries: 4572
- Incidents: 181

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 46 | HOT | 8.01 |
| 50 | HOT | 8.00 |

## HOT query latency

- Responded: 3935 / 4572
- Avg latency (responded >100ms): 3411.0 ms
- p95: 9690 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 729 | 678 | 93% |
| ambiguous | 690 | 538 | 78% |
| burst | 378 | 305 | 81% |
| edge_case | 378 | 264 | 70% |
| multilingual | 576 | 459 | 80% |
| normal | 1222 | 1145 | 94% |
| structured | 599 | 546 | 91% |

## Incidents by category

| category | count |
|---|---|
| chat_response_failure | 154 |
| health_failure | 12 |
| chat_failure | 8 |
| auth_recovery_failure | 3 |
| cycle_crash_recovery | 3 |
| chat_send_failure | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
