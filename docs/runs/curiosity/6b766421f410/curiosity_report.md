# Gap miner — curiosity report

- **Schema version:** 1
- **Campaign dir:** `docs/runs/ui_gauntlet_400h_20260413_092800`
- **Pin hash:** `sha256:6b766421f41032d0d2278de7f89716be9da2f827987f7fadf7aeb9e7f142c46b`
- **Rows scanned:** 35445
- **Rows unresolved:** 7077
- **Rows high-latency:** 8760
- **Curiosity items:** 100

## Counts by gap type

| gap_type | count |
|---|---|
| `improvement_opportunity` | 2 |
| `low_confidence_routing` | 89 |
| `missing_solver` | 6 |
| `subdivision_pressure` | 3 |

## Counts by recommended action

| action | count |
|---|---|
| `clarify_routing` | 89 |
| `improve_solver` | 2 |
| `propose_solver` | 6 |
| `propose_subdivision` | 3 |

## Counts by candidate cell

| cell | count |
|---|---|
| `_unattributed` | 96 |
| `safety` | 1 |
| `seasonal` | 1 |
| `system` | 2 |

## Top curiosity items (by estimated value)

| rank | curiosity_id | cell | gap_type | evidence | value | action |
|---|---|---|---|---|---|---|
| 1 | `cur_aad2aaead85bf32c` | `—` | `low_confidence_routing` | high | 1396.0 | `clarify_routing` |
| 2 | `cur_ace31e4c864f954a` | `—` | `missing_solver` | high | 386.0 | `propose_solver` |
| 3 | `cur_c891fa77624a3345` | `—` | `low_confidence_routing` | high | 336.0 | `clarify_routing` |
| 4 | `cur_f547159c987820b1` | `—` | `low_confidence_routing` | high | 252.0 | `clarify_routing` |
| 5 | `cur_da0e11052f4998d1` | `—` | `low_confidence_routing` | high | 91.0 | `clarify_routing` |
| 6 | `cur_84810af13fe5a843` | `—` | `low_confidence_routing` | high | 69.0 | `clarify_routing` |
| 7 | `cur_1db6202525fdde48` | `—` | `low_confidence_routing` | high | 69.0 | `clarify_routing` |
| 8 | `cur_252308e148e91d16` | `—` | `low_confidence_routing` | high | 67.0 | `clarify_routing` |
| 9 | `cur_fc662b7bde6357a7` | `system` | `subdivision_pressure` | high | 65.0 | `propose_subdivision` |
| 10 | `cur_2e4c70e322e1ccfe` | `—` | `low_confidence_routing` | high | 61.0 | `clarify_routing` |
| 11 | `cur_2e87e354ec61d8f5` | `—` | `improvement_opportunity` | high | 61.0 | `improve_solver` |
| 12 | `cur_cd8170dede64009d` | `—` | `low_confidence_routing` | high | 58.0 | `clarify_routing` |
| 13 | `cur_9af402f06bc526cb` | `—` | `low_confidence_routing` | high | 53.0 | `clarify_routing` |
| 14 | `cur_11a98b08baf37d56` | `—` | `missing_solver` | high | 50.0 | `propose_solver` |
| 15 | `cur_d4479a0952558444` | `—` | `low_confidence_routing` | high | 50.0 | `clarify_routing` |
| 16 | `cur_987d8cc641c9d6ce` | `—` | `improvement_opportunity` | high | 50.0 | `improve_solver` |
| 17 | `cur_4429bea4275027e8` | `—` | `missing_solver` | high | 45.0 | `propose_solver` |
| 18 | `cur_01eae00f40e8e7bd` | `—` | `missing_solver` | high | 40.0 | `propose_solver` |
| 19 | `cur_34c520c1db27c902` | `—` | `missing_solver` | high | 39.0 | `propose_solver` |
| 20 | `cur_840a03b5cf36bf0f` | `—` | `low_confidence_routing` | high | 39.0 | `clarify_routing` |
