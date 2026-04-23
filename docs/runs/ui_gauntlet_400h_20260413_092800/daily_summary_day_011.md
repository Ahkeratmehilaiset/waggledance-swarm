# Daily Summary — Day 011 (2026-04-23)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T23:35:29+00:00

## Totals this day

- Green hours: 16.01h
- Cumulative: 143.10h / 400h
- Segments completed: 2
- HOT queries: 6646
- Incidents: 211

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 46 | HOT | 8.01 |
| 50 | HOT | 8.00 |

## HOT query latency

- Responded: 5940 / 6646
- Avg latency (responded >100ms): 3295.0 ms
- p95: 6527 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 1134 | 1070 | 94% |
| ambiguous | 966 | 814 | 84% |
| burst | 546 | 467 | 86% |
| edge_case | 566 | 402 | 71% |
| multilingual | 896 | 779 | 87% |
| normal | 1698 | 1621 | 95% |
| structured | 840 | 787 | 94% |

## Incidents by category

| category | count |
|---|---|
| chat_response_failure | 159 |
| health_failure | 32 |
| chat_failure | 8 |
| auth_recovery_failure | 3 |
| cycle_crash_recovery | 3 |
| backend_unhealthy | 3 |
| chat_send_failure | 2 |
| cookie_failure | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
