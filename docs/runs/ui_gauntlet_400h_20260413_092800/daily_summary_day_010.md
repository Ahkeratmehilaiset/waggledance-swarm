# Daily Summary — Day 010 (2026-04-22)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T16:04:11+00:00

## Totals this day

- Green hours: 8.00h
- Cumulative: 127.09h / 400h
- Segments completed: 1
- HOT queries: 2731
- Incidents: 138

## Segments completed

| seg | mode | green_h |
|---|---|---|
| 42 | HOT | 8.00 |

## HOT query latency

- Responded: 2281 / 2731
- Avg latency (responded >100ms): 4376.0 ms
- p95: 14817 ms

### Per-bucket send/respond

| bucket | total | responded | rate |
|---|---|---|---|
| adversarial | 486 | 450 | 93% |
| ambiguous | 347 | 242 | 70% |
| burst | 252 | 164 | 65% |
| edge_case | 252 | 169 | 67% |
| multilingual | 384 | 330 | 86% |
| normal | 650 | 600 | 92% |
| structured | 360 | 326 | 91% |

## Incidents by category

| category | count |
|---|---|
| chat_response_failure | 98 |
| health_failure | 15 |
| chat_failure | 8 |
| cycle_crash_recovery | 6 |
| backend_unhealthy | 3 |
| tab_switch_failure | 3 |
| auth_recovery_failure | 2 |
| chat_send_failure | 2 |
| context_recycle_failure | 1 |

## Safety gates (zero tolerance per x.txt)

- XSS hits: 0
- DOM breaks: 0
