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
| runtime (s, informational) | 0.035 | 0.046 |

Disabled mode returns `None` for every query (chat falls through to
the ordinary pipeline), so the enabled column shows what the mesh
actually adds: local resolution and the escalation ladder. Observed
fixture limitation: the neighbor-assist rung is not exercised by
this v1 workload (neighbor counters stay 0); a future fixture with
cross-domain queries + tuned confidence bands should cover it.

Rerun: `python tools/run_hex_routing_benchmark.py` — the
`deterministic_views` block is byte-comparable between runs.
