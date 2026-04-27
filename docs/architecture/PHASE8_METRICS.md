# Phase 8 — Capability-growth metrics

- **Status:** documented; reporting implemented as a non-runtime tool. Live counters are NOT added on this branch because the production server is mid-campaign (`ui_gauntlet_400h_20260413_092800`) and changing the Prometheus surface would invalidate the in-flight baseline.

## Why non-runtime first

The existing `/metrics` endpoint (`waggledance/adapters/http/routes/metrics.py`) exposes 15 counters and 4 gauges tied to the hex-mesh assist pipeline. Adding new counters without a pass over the producer wiring risks:

- breaking Prometheus scrape parsing if a metric name overlaps with an existing one
- double-counting if the same event is observed in two layers
- muddling the running campaign's measurement window, which is the source of the only hard data we have about hybrid-retrieval Phase D-1 behavior

Instead, Phase 8 ships a **reporting script** that computes the capability-growth signals from the existing artifacts on disk (axiom YAMLs, cell manifests, hot_results.jsonl, composition-graph output). Numbers are the same in spirit as live counters; they're just computed offline. When the campaign finishes and we're ready to add live wiring, the same field names carry over.

## The 13 signals

Names match the x.txt Phase 8 list. Each is computable today from artifacts we already produce or expose through the phase 2-7 tools.

| Signal | Type | Source |
|---|---|---|
| `solver_count_by_cell` | gauge | `tools/cell_manifest.py` per-cell JSON |
| `solver_route_depth` | histogram | `hot_results.jsonl` → `route.layers` field length |
| `solver_route_latency_ms` | histogram | `hot_results.jsonl` → `latency_ms` per cell |
| `llm_fallback_rate_by_cell` | gauge | `hot_results.jsonl` filtered by keyword → `route_layer == "llm_fallback"` |
| `useful_composite_paths` | gauge | `tools/solver_composition_report.py` → bridges with score ≥ 1.0 |
| `duplicate_solver_rejections` | counter | `tools/propose_solver.py` gate history (today: [] until proposals land) |
| `contradiction_rejections` | counter | same — emitted by the proposal gate |
| `proposal_gate_verdicts` | counter | same — emitted by the proposal gate |
| `cell_gap_score` | gauge | cell manifest `gap_score` |
| `cell_entropy_score` | gauge | composition graph — count of distinct output units per cell |
| `dream_bridge_candidates` | gauge | composition graph — total `bridges` count |
| `teacher_proposals_generated` | counter | bookkeeping in the teacher driver (out of scope for this branch) |
| `teacher_proposals_accepted_shadow` | counter | proposal-gate verdicts → `ACCEPT_SHADOW_ONLY` |
| `teacher_proposals_promoted` | counter | nightly promotion pipeline (out of scope for this branch) |

## Reporting script

`tools/phase8_capability_report.py` emits `docs/runs/phase8_capability_report.md` plus an optional `--json` summary. It reads:

- `configs/axioms/` for the current library
- `docs/cells/<cell>/manifest.json` for manifest fields
- `docs/runs/ui_gauntlet_400h_*/hot_results.jsonl` for production latencies and fallback rate
- `waggledance.core.learning.composition_graph.build_graph` for bridges and entropy

The script is pure and has no network dependencies. It is safe to run in parallel with the live campaign.

### Fields in the report

```
solver_count_by_cell
  general: ...
  thermal: ...
  ...
solver_route_depth
  p50: ...
  p95: ...
solver_route_latency_ms
  p50: ...
  p95: ...
  per_cell:
    thermal: {p50, p95}
    ...
llm_fallback_rate_by_cell
  thermal: ...
  ...
useful_composite_paths
  total: ...
  by_depth: {2: ..., 3: ..., 4: ...}
cell_gap_score
  thermal: ...
  ...
cell_entropy_score
  thermal: ...
  ...
dream_bridge_candidates
  total: ...
proposal_gate_verdicts
  (empty until proposals land)
```

### Thresholds to watch

These are the numbers a human reviewer should treat as "flag for discussion":

- any `llm_fallback_rate_by_cell >= 0.3` sustained for 24 h → Phase 7 trigger candidate
- any `cell_gap_score >= 0.7` → manifest is signalling the teacher should work this cell next
- any `cell_entropy_score >= 6` → Phase 7 subdivision trigger
- `useful_composite_paths` monotonically increasing = healthy scaling; flat or decreasing = library pollution

## When to add live counters

- Campaign ends cleanly (target hours reached, no open kill signals).
- At least one full 12 h preventive-restart cycle has confirmed the zombie-reap fix does not regress.
- The Phase 9 honeycomb campaign harness has run through at least one shakedown segment.

At that point, the new counter block for `metrics.py` lives in this same branch's followup commit — not now.

## Non-goals

- No new `/metrics` exposure on this branch.
- No shared-state mutation (the report is read-only over disk artifacts).
- No derivation of `teacher_proposals_generated` — that requires the driver loop which is out of scope for Phase 8.
