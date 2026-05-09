# R18 hex latency scout - 2026-05-09

- timestamp: 2026-05-09T17:59:00Z
- model: Codex GPT-5
- task: Phase D Priority 2 scout after MAGMA latency first round
- priority order: hexagon delays > 10k+ solver scaling blockers

## Repeatable microbenchmark

Artifacts added by this scout:

- `iterations/codex_scout_tasks/hex_latency_microbench_2026_05_09.py`
- `iterations/codex_scout_tasks/hex_latency_snapshot_2026_05_09.json`

Reproduce:

```powershell
.\.venv\Scripts\python.exe iterations\codex_scout_tasks\hex_latency_microbench_2026_05_09.py --out-json .codex-audit\r18_hex_latency_microbench.json
```

Snapshot hash from this run: `2a03ff973bf1`. Keep the snapshot manifest unchanged for before/after comparisons.

The script is pure Python and uses no provider, LLM, Ollama, ChromaDB, network path, or product data writes.

Measured on this local run:

| Operation | Count | Latency |
|---|---:|---:|
| `ring_messaging.deliver_batch ring_request` | 20000 | 95.59 ms total |
| `ring_messaging.deliver_batch child_to_parent` | 20000 | 69.09 ms total |
| `parent_child_relations.neighbors_of repeated` | 20000 | 19.16 ms total |
| `HexTopologyRegistry.get_neighbor_cells repeated` | 20000 | 253.28 ms total |
| `HexTopologyRegistry.select_origin_cell repeated` | 2000 | 65.19 ms total |

Local target tests used as a safety baseline:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_phase9_hex_topology.py tests\test_hex_mesh.py -q --basetemp=.codex-audit\pytest-r18-hex-scout
```

Result: `100 passed in 1.74s`.

## Candidate 1: Cache registry neighbor IDs

- Target: `waggledance/application/services/hex_topology_registry.py :: HexTopologyRegistry.get_neighbor_cells`
- Current coverage: partial; `tests/test_hex_mesh.py` verifies basic neighbor counts and hub shape, but not repeated lookup cost or cache invalidation semantics.
- Evidence: `get_neighbor_cells()` recomputes `cell.coord.neighbors()` and does six coordinate lookups on every call. The microbench measured 20k repeated lookups at `253.28 ms`, which is the largest measured pure hex hotspot. Runtime path: `HexNeighborAssist._try_neighbors()` calls this once for each low-confidence query before consulting neighbors.
- Proposed test/change: build a deterministic `_neighbor_cell_ids: dict[str, tuple[str, ...]]` during `_load()` after cells are parsed, then have `get_neighbor_cells()` map cached IDs to enabled `HexCellDefinition` objects. Tests should assert current ordering, disabled-cell filtering, missing-cell behavior, and a scale guard using a synthetic registry object.
- Estimated test size: 50-90 LoC.
- Why it matters: neighbor lookup is the first ring-hop planning step. It is pure topology metadata, so it should be a local cache hit per hex instead of rebuilding axial neighbor coordinates on every query.
- Risk if missing: medium. The current default 7-cell config is fine, but the path scales linearly with query count and does avoidable allocation before every neighbor assist. In a 10k-solver swarm, this becomes control-plane jitter exactly where routing should stay cheap.

## Candidate 2: Batch relation index for ring messaging

- Target: `waggledance/core/hex_topology/ring_messaging.py :: deliver_batch / deliver_one`
- Current coverage: partial; `tests/test_phase9_hex_topology.py` verifies correctness/order for small batches, not scale or repeated relation lookup cost.
- Evidence: `deliver_batch()` calls `deliver_one()` per message. Ring messages call `neighbors_of()`, and `neighbors_of()` sorts the neighbor list every time. The microbench measured `ring_request` batch delivery at `95.59 ms` for 20k messages and isolated repeated `neighbors_of()` at `19.16 ms`.
- Proposed test/change: keep `deliver_one()` unchanged for compatibility, but make `deliver_batch()` precompute per-topology relation indexes for the batch: `neighbor_sets`, `parent_by_cell`, and `child_sets`. Use set membership for validation while preserving sequence numbers and blocked reasons. Add a parity test comparing old-style `deliver_one()` results with new `deliver_batch()` results for mixed message kinds.
- Estimated test size: 60-100 LoC.
- Why it matters: ring messaging is the pure core for cell-to-cell communication. A batch-level index removes repeated sorting and map lookups without changing message contracts.
- Risk if missing: medium. High-volume ring bursts pay repeated validation overhead and make message delivery cost grow with repeated relation queries instead of batch-local precomputation.

## Candidate 3: Selector index for origin-cell routing

- Target: `waggledance/application/services/hex_topology_registry.py :: HexTopologyRegistry.select_origin_cell`
- Current coverage: partial; tests verify a few domain examples, but not scale or tie-breaking stability.
- Evidence: `select_origin_cell()` scans every enabled cell and every domain/tag selector for each query. The default config has only seven cells, yet 2000 repeated selections measured `65.19 ms`. With many specialized cells, this is an O(cells * selectors) routing step before local/neighbor solving begins.
- Proposed test/change: build a lowercased selector index at load time and use query token/substring matches to produce a candidate set before falling back to the full scan. Preserve current tie-breaking by scoring candidates with the same formula and using the old full scan when no indexed selector matches.
- Estimated test size: 70-100 LoC.
- Why it matters: origin selection is the entry point for every hex query. If the topology grows from seven domain cells toward many solver-family cells, the current scan becomes a repeated routing tax.
- Risk if missing: high for future 10k+ topology, medium for current config. It is the clearest bridge between Priority 2 hex latency and Priority 3 solver-scale blockers.

## Not first: async neighbor execution

`HexNeighborAssist._try_neighbors()` already has a parallel dispatcher branch, but the sequential fallback intentionally limits itself to one neighbor and can still perform an LLM call. That path is important, but not the first measurable PR because a safe benchmark would need a fake dispatcher/LLM harness and more behavioral assertions. The pure topology caches above are smaller and easier to validate in one 30-90 minute PR.

## Self-assessment

Pick Candidate 1 first. It has the largest measured pure hex latency in the scout (`253.28 ms` per 20k lookups), maps directly to the runtime neighbor-assist path, and should fit one focused PR without changing public behavior. Candidate 2 is the better pure-core ring-messaging improvement, but it needs more parity testing to avoid changing blocked-reason semantics. Candidate 3 is strategically important for 10k+ scale, but it is a larger behavior-preservation problem because selector scoring and tie-breaking are user-visible routing behavior.
