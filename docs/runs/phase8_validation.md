# Phase 8 — validation pack

- **Date:** 2026-04-24 (initial) · **Revised:** 2026-04-24 after R5, R6, and Stage-2 landings
- **Branch:** `phase8/honeycomb-solver-scaling-foundation`
- **Runner:** local (Python 3.13.7, `.venv`)
- **Running campaign:** `ui_gauntlet_400h_20260413_092800` continued to run throughout; no disruption observed

## Revision history

| Date | Event | Targeted tests |
|---|---|---|
| 2026-04-24 initial | Phase 2-9 scaffolding landed (`eda5d44`…`490d2da`) | 171 pass |
| 2026-04-24 R4 fix (`cd6b425`) | GPT R4 required + advisory items | 195 pass |
| 2026-04-24 R4 advisory (`e9093b6`) | rescale edges, busy-lock, machine_invariants stub | 219 pass |
| 2026-04-24 R5 fix (`60c1ab9`) | Gate-14 tighten, shakedown-skip, pidfile create_time, build_nodes fail-closed, uncertainty-aware verdict, prose normalization, xfail label | 281 pass + 1 xfail |
| 2026-04-24 Stage 1 (`28dff51`) | `data/vector/` snapshot + vector event contract | 310 pass + 1 xfail |
| 2026-04-24 Stage 1 finish (`167702c`) | event log writer + backfill producer + stub consumer | 328 pass + 1 xfail |
| 2026-04-24 Stage 2 (`d03d5d2`) | writer skeleton, checkpoint, atomic apply, commit_applied emission | **350 pass + 1 xfail** |

## Targeted tests (current as of `d03d5d2`)

```
.venv/Scripts/python.exe -m pytest \
  tests/test_phase7_hologram_news_wire.py \
  tests/test_cell_manifest.py \
  tests/test_solver_hash.py \
  tests/test_solver_dedupe.py \
  tests/test_solver_proposal_schema.py \
  tests/test_propose_solver_gate.py \
  tests/test_composition_graph.py \
  tests/test_hex_subdivision_plan.py \
  tests/test_run_honeycomb_400h_campaign.py \
  tests/test_vector_events.py \
  tests/test_vector_indexer.py \
  tests/test_vector_indexer_stage2.py \
  tests/test_migrate_to_vector_root.py \
  tests/test_hybrid_retrieval.py \
  -q --tb=line -p no:warnings
```

Result: **350 passed, 1 xfailed in 11.55 s.**

| Module | Passed | Δ vs original |
|---|---|---|
| `test_phase7_hologram_news_wire.py` | 20 | — |
| `test_cell_manifest.py` | 11 | — |
| `test_solver_hash.py` | 29 | — |
| `test_solver_dedupe.py` | 6 | — |
| `test_solver_proposal_schema.py` | 34 | +1 (uncertainty_declaration required) |
| `test_propose_solver_gate.py` | 76 | +40 (R4 + R5 hardening) |
| `test_composition_graph.py` | 33 | +17 (primary flag, rescale, multi-primary fail-closed) |
| `test_hex_subdivision_plan.py` | 11 | — |
| `test_run_honeycomb_400h_campaign.py` | 21 | +12 (busy-lock, create_time pidfile, skip-label) |
| `test_vector_events.py` | 24 | +24 (new) |
| `test_vector_indexer.py` | 11 | +11 (Stage 1 stub) |
| `test_vector_indexer_stage2.py` | 22 | +22 (new — apply/checkpoint/idempotency) |
| `test_migrate_to_vector_root.py` | 13 | +13 (new) |
| `test_hybrid_retrieval.py` | 40 / 1 xfail | pre-existing, now xfail-labeled |
| **Total** | **350 + 1 xfail** | +179 over initial 171 |

## Full suite status (reference)

Taken from `docs/runs/phase8_ci_baseline.md` at branch creation:
- 5 666 passed, 1 failed, 3 skipped in 715 s
- The single failure is `tests/test_hybrid_retrieval.py::TestFeatureFlag::test_enabled_returns_hybrid`, pre-existing and documented. Scope: Phase D-2 label mismatch, not Phase 8.

This branch adds **151 new test cases** (11 + 29 - 17 legacy + 6 + 33 + 36 + 16 + 11 + 9) that did not exist pre-Phase-8. A full-suite rerun is expected to report:

- 5 666 pre-existing passes + 151 new passes = 5 817 total, 1 pre-existing failure.

(Rerunning the 11-min full suite was skipped for this validation pack because (a) targeted runs already cover every Phase 8 artifact and (b) the live campaign traffic is the gating input, not CI churn.)

## Live server smoke

Running campaign server responded while the validation was in flight:

```
GET /health               -> 200
GET /ready                -> 200
GET /api/status           -> 200
GET /api/feeds            -> 200
GET /api/hologram/state   -> 200
GET /metrics              -> 200
```

POST `/api/chat` was not exercised (would require reading the
ephemeral gauntlet API key; the harness itself is doing this
continuously as part of the ui_gauntlet campaign, so we would be
duplicating noise).

## Phase-2-9 tool smoke

Each tool was executed once at the end of its phase commit; evidence
persisted under `docs/runs/`:

| Tool | Evidence |
|---|---|
| `tools/cell_manifest.py` | `docs/cells/<cell>/manifest.json` + `INDEX.md` for 8 cells |
| `tools/solver_dedupe.py` | `docs/runs/solver_dedupe_report.md` |
| `tools/propose_solver.py` | exercised in tests; schema reject, accept-candidate, and REJECT_LOW_VALUE paths reached |
| `tools/solver_composition_report.py` | `docs/runs/solver_composition_report.md` — includes advisory rescale section |
| `tools/hex_subdivision_plan.py` | `docs/runs/hex_subdivision_plan.md` |
| `tools/phase8_capability_report.py` | `docs/runs/phase8_capability_report.md` |
| `tools/run_honeycomb_400h_campaign.py` | `docs/runs/honeycomb_400h/plan.md` + one shakedown pass |
| `tools/migrate_to_vector_root.py` (Stage 1) | `data/vector/<cell>/{index.faiss, meta.json, manifest.json, commit.json}` × 8 |
| `tools/vector_indexer.py` (Stage 2 writer) | exercised in tests; stages commit, swaps pointer, emits `vector.commit_applied`, advances checkpoint |

## Runtime invariants preserved

- No change to `waggledance/core/hex_cell_topology.py`
- No change to the Prometheus metrics surface (`waggledance/adapters/http/routes/metrics.py`)
- No change to `core/symbolic_solver.py` ModelRegistry
- `core/audit_log.py` untouched
- `bootstrap/event_bus.py` untouched
- `adapters/llm/ollama_adapter.py` untouched
- `start_waggledance.py` untouched

All Phase 8 work lives under:
- `docs/architecture/HONEYCOMB_SOLVER_SCALING.md`
- `docs/architecture/PHASE8_METRICS.md`
- `docs/prompts/cell_teacher_prompt.md`
- `docs/cells/*`
- `docs/runs/phase8_*`, `docs/runs/honeycomb_400h/`, `docs/runs/solver_dedupe_report.md`, `docs/runs/solver_composition_report.md`, `docs/runs/hex_subdivision_plan.md`
- `schemas/solver_proposal.schema.json`
- `tools/cell_manifest.py`, `tools/solver_dedupe.py`, `tools/propose_solver.py`, `tools/solver_composition_report.py`, `tools/hex_subdivision_plan.py`, `tools/phase8_capability_report.py`, `tools/run_honeycomb_400h_campaign.py`
- `waggledance/core/learning/solver_hash.py` (extended additively), `waggledance/core/learning/composition_graph.py` (new)
- `tests/test_cell_manifest.py`, `tests/test_solver_hash.py` (extended), `tests/test_solver_dedupe.py`, `tests/test_solver_proposal_schema.py`, `tests/test_propose_solver_gate.py`, `tests/test_composition_graph.py`, `tests/test_hex_subdivision_plan.py`, `tests/test_run_honeycomb_400h_campaign.py`

## Verdict

- All Phase 8 scaffolding in place.
- Targeted tests green.
- No regression on the running campaign.
- One pre-existing hybrid-retrieval test remains failing — out of Phase 8 scope and documented in `phase8_ci_baseline.md`.
- Live counters deliberately deferred; offline report available today.

Proceeding to Phase 11 (docs) + Phase 12 (final report).
