# Daily Summary — Day 011 (2026-04-23)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T12:40:48+00:00

## Totals this day

- Green hours: 8.01h
- Cumulative: 135.10h / 400h
- Segments completed: 1
- HOT queries: 2799
- Incidents: 172

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 46 | HOT | 8.01 |

## HOT query latency

- Responded: 2203 / 2799
- Avg latency (responded >100ms): 3908.0 ms
- p95: 12946 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 486 | 440 | 91% |
| ambiguous | 414 | 262 | 63% |
| burst | 244 | 173 | 71% |
| edge_case | 252 | 171 | 68% |
| multilingual | 384 | 267 | 70% |
| normal | 659 | 583 | 88% |
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
