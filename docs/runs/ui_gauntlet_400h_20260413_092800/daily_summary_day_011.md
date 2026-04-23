# Daily Summary — Day 011 (2026-04-23)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T13:10:53+00:00

## Totals this day

- Green hours: 16.01h
- Cumulative: 143.10h / 400h
- Segments completed: 2
- HOT queries: 3009
- Incidents: 172

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 46 | HOT | 8.01 |
| 50 | HOT | 8.00 |

## HOT query latency

- Responded: 2413 / 3009
- Avg latency (responded >100ms): 3819.0 ms
- p95: 12816 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 486 | 440 | 91% |
| ambiguous | 465 | 313 | 67% |
| burst | 252 | 181 | 72% |
| edge_case | 252 | 171 | 68% |
| multilingual | 384 | 267 | 70% |
| normal | 810 | 734 | 91% |
| structured | 360 | 307 | 85% |

## Incidents by category

| category | count |
|---|---|
| chat_response_failure | 152 |
| chat_failure | 8 |
| health_failure | 5 |
| auth_recovery_failure | 3 |
| cycle_crash_recovery | 3 |
| chat_send_failure | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
