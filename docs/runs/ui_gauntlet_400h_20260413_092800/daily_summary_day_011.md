# Daily Summary — Day 011 (2026-04-23)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T14:11:02+00:00

## Totals this day

- Green hours: 16.01h
- Cumulative: 143.10h / 400h
- Segments completed: 2
- HOT queries: 3401
- Incidents: 173

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 46 | HOT | 8.01 |
| 50 | HOT | 8.00 |

## HOT query latency

- Responded: 2793 / 3401
- Avg latency (responded >100ms): 3686.0 ms
- p95: 11822 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 567 | 520 | 92% |
| ambiguous | 483 | 331 | 69% |
| burst | 294 | 223 | 76% |
| edge_case | 294 | 202 | 69% |
| multilingual | 448 | 331 | 74% |
| normal | 895 | 819 | 92% |
| structured | 420 | 367 | 87% |

## Incidents by category

| category | count |
|---|---|
| chat_response_failure | 152 |
| chat_failure | 8 |
| health_failure | 6 |
| auth_recovery_failure | 3 |
| cycle_crash_recovery | 3 |
| chat_send_failure | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
