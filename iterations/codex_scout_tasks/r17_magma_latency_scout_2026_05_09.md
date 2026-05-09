# R17 MAGMA latency scout - 2026-05-09

- timestamp: 2026-05-09T16:10:00Z
- model: Codex GPT-5
- task: Phase D first scout after R15 bridge bootstrap/stale-lease close
- priority order: MAGMA latencies > hexagon delays > 10k+ solver scaling blockers

## Repeatable microbenchmark

Artifacts added by this scout:

- `iterations/codex_scout_tasks/magma_latency_microbench_2026_05_09.py`
- `iterations/codex_scout_tasks/magma_latency_snapshot_2026_05_09.json`

Reproduce:

```powershell
.\.venv\Scripts\python.exe iterations\codex_scout_tasks\magma_latency_microbench_2026_05_09.py --out-json .codex-audit\r17_magma_latency_microbench.json
```

Snapshot hash from the run: `bb3e93036f3e`. Keep the snapshot manifest unchanged for before/after comparisons.

Measured on this local run:

| Operation | Count | Latency |
|---|---:|---:|
| `EventLogAdapter.log_event` bulk | 5000 | 81.41 ms total |
| `AuditProjector.record` bulk with no-op legacy audit | 500 | 3.21 ms total |
| `TrustAdapter.record_observation` bulk | 40960 | 252.84 ms total |
| `TrustAdapter.get_trust_score` | 512 | 0.0367 ms p50 / 0.0635 ms p99 |
| `TrustAdapter.get_ranking` | 512 targets | 20.44 ms total |
| `ReplayAdapter.record_mission_event` bulk | 1600 | 5.40 ms total |
| `ReplayAdapter.list_missions` | 200 missions | 0.59 ms total |
| `vector_events.emit_many` JSONL | 10000 events | 201.17 ms total |
| `vector_events.read_events` full scan | 10000 events | 159.74 ms total |

The script uses no provider, model, Ollama, ChromaDB, or network path. Legacy audit/ledger adapters are explicit no-ops so the benchmark does not write production data.

## Candidate 1: Trust ranking single-pass hot path

- Target: `waggledance/core/magma/trust_adapter.py :: TrustAdapter.get_all_scores / get_ranking`
- Current coverage: partial; `tests/autonomy/test_magma_adapters.py` covers basic ranking shape, not scale or latency behavior.
- Evidence: `get_all_scores` iterates every key and calls `get_trust_score` per target (`trust_adapter.py:143-156`); `get_trust_score` then reacquires the lock and scans that target's observations (`trust_adapter.py:112-137`). The microbench already crosses 10 ms at only 512 solver targets: `get_ranking` took 20.44 ms.
- Proposed test/change: add a direct test that loads many solver observations and verifies a new single-pass ranking helper returns the same ordering as the current implementation. Implementation should snapshot `_observations` once under lock, use one shared `now`, and compute scores without nested method calls.
- Estimated test size: 40-80 LoC.
- Why it matters: solver trust ranking is on the path that decides which learned solver is worth using or promoting. At 10k solvers, the current shape trends toward 10k repeated score scans and repeated lock acquisition.
- Risk if missing: high. Ranking can become a >100 ms control-plane pause as solver count grows, causing slow routing or stale trust decisions.

## Candidate 2: Vector-event checkpoint reader

- Target: `waggledance/core/magma/vector_events.py :: read_events`
- Current coverage: partial; vector event tests validate parsing and event shape, but the primitive reader is a full JSONL scan.
- Evidence: `read_events` opens the file and parses every line (`vector_events.py:285-305`). The microbench full scan of 10000 events took 159.74 ms; `emit_many` took 201.17 ms.
- Proposed test/change: add a checkpoint-aware reader such as `read_events_from_offset(path, byte_offset=0)` returning parsed events plus the next byte offset, or an equivalent small record wrapper. Keep `read_events` behavior unchanged. Tests should prove a second read from the returned offset only parses newly appended rows.
- Estimated test size: 50-90 LoC.
- Why it matters: Stage 2 vector indexing is explicitly event-sourced. A consumer that repeatedly replays the full file will get slower with every solver update burst.
- Risk if missing: high. The vector projection path can turn into an O(total_log_size) poll loop instead of O(new_events), which is exactly the wrong scaling shape for 10k+ solvers.

## Candidate 3: EventLogAdapter buffer churn

- Target: `waggledance/core/magma/event_log_adapter.py :: EventLogAdapter.log_event`
- Current coverage: partial; tests verify queries and counts, not bounded-buffer churn.
- Evidence: after `_max_buffer`, each append does `self._buffer = self._buffer[-self._max_buffer:]` (`event_log_adapter.py:78-80`). With the default 1000-entry cap, the 5000-event benchmark performed 4000 tail-copy trims and took 81.41 ms total.
- Proposed test/change: replace the list buffer with `collections.deque(maxlen=1000)` or trim in larger chunks, while preserving query ordering and stats behavior. Test that >max events keeps the newest events and query/count semantics remain stable.
- Estimated test size: 30-60 LoC.
- Why it matters: this adapter is the simple MAGMA event intake surface. It is not the worst latency today, but it is a cheap improvement and reduces avoidable allocation at burst write rates.
- Risk if missing: medium. Sustained event bursts waste CPU on list copies and can add jitter to the autonomy loop.

## Hexagon latency note

`ring_messaging.deliver_batch` calls `deliver_one` per message, and neighbor validation calls `neighbors_of`, which sorts each neighbor list on every message (`ring_messaging.py:43`, `parent_child_relations.py:31-35`). This is not the first PR because MAGMA has clearer measured >10 ms paths, but the likely future hex improvement is to precompute neighbor sets per topology snapshot.

## 10k+ solver scaling note

Existing Phase 17A tests already prove the real 10k capability lookup path through `RuntimeQueryRouter` and `ControlPlaneDB`. The new risk from this scout is adjacent: MAGMA trust/vector bookkeeping around those solvers has no equivalent 10k regression guard yet. Candidate 1 or 2 should become the first measurable Phase D improvement PR.

## Self-assessment

Pick Candidate 1 first. It is pure Python, directly tied to solver selection quality, already crosses the 10 ms threshold at 512 targets, and should fit a 30-90 minute PR with a clear before/after benchmark. Candidate 2 is probably the more important architectural fix for event sourcing, but byte-offset checkpoint semantics need more care to avoid corrupting the existing reader contract.
