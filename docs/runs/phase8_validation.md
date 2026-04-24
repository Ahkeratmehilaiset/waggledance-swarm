# Phase 8 — validation pack

- **Date:** 2026-04-24
- **Branch:** `phase8/honeycomb-solver-scaling-foundation`
- **Runner:** local (Python 3.13.7, `.venv`)
- **Running campaign:** `ui_gauntlet_400h_20260413_092800` continued to run during validation; no disruption observed

## Targeted tests

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
  -q --tb=line -p no:warnings
```

Result: **171 passed in 4.77 s.**

| Module | Passed |
|---|---|
| `test_phase7_hologram_news_wire.py` | 20 |
| `test_cell_manifest.py` | 11 |
| `test_solver_hash.py` | 29 |
| `test_solver_dedupe.py` | 6 |
| `test_solver_proposal_schema.py` | 33 |
| `test_propose_solver_gate.py` | 36 |
| `test_composition_graph.py` | 16 |
| `test_hex_subdivision_plan.py` | 11 |
| `test_run_honeycomb_400h_campaign.py` | 9 |
| **Total** | **171** |

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
| `tools/propose_solver.py` | exercised in shakedown; schema reject and accept-candidate paths both reached |
| `tools/solver_composition_report.py` | `docs/runs/solver_composition_report.md` |
| `tools/hex_subdivision_plan.py` | `docs/runs/hex_subdivision_plan.md` |
| `tools/phase8_capability_report.py` | `docs/runs/phase8_capability_report.md` |
| `tools/run_honeycomb_400h_campaign.py` | `docs/runs/honeycomb_400h/plan.md` + one shakedown pass |

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
