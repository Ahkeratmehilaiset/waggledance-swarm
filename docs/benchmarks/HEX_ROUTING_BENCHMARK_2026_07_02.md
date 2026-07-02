# Hex routing benchmark (sprint seed #11)

Topology: `configs/hex_cells.yaml` — 7 cells, synthetic agents, deterministic fake LLM (no network).

Workload: 42 queries (6 per category × 7 categories).

| metric | hex_mesh disabled | hex_mesh enabled |
|---|---|---|
| resolved by hex mesh | 0 | 12 |
| fell back to non-hex path | 42 | 30 |
| LLM calls | 0 | 16 |
| local-only resolutions | 0 | 12 |
| neighbor-assist resolutions | 0 | 0 |
| global escalations | 0 | 30 |
| runtime (s, informational) | 0.039 | 0.046 |

Disabled mode returns `None` for every query (chat falls through to
the ordinary pipeline), so the enabled column shows what the mesh
actually adds: local resolution and the escalation ladder.

## Neighbor-assist rung (v2)

The v1 enabled workload never reached the neighbor-assist rung (counter stayed 0) because hedged local answers were low-preflight and got skipped straight to global. This fixture opens the preflight gate and supplies weak local answers + a fixed high-confidence neighbor dispatcher, so the ladder actually runs local → neighbor:

| metric | neighbor-assist fixture |
|---|---|
| resolved via neighbor path | 12 |
| neighbor-assist resolutions | 12 |
| local-only resolutions | 0 |
| completed neighbor batches | 42 |
| global escalations | 30 |

This is an isolated fixture (not the production default gating); it demonstrates the routing path exists and increments its counters, closing the v1 limitation.

Rerun: `python tools/run_hex_routing_benchmark.py` — the
`deterministic_views` block is byte-comparable between runs.
