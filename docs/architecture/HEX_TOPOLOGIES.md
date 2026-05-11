# Hex topologies in WaggleDance — there are two, and they are independent

**Audience**: anyone reading the codebase who sees "hex cells" and is
unsure which one is which. Audit findings H4, H14, and H17 surfaced
that the project has **two distinct hex-cell topologies** that are
sometimes conflated in docs (including a paragraph-internal
inconsistency in README.md). This document is the single source of
truth that disambiguates them.

---

## Quick reference

| | **Agent-routing topology** | **Solver-retrieval topology** |
|---|---|---|
| **Cell count** | 7 | 8 |
| **Source of truth** | `configs/hex_cells.yaml` | `waggledance/core/hex_cell_topology.py` |
| **Loader** | `HexTopologyRegistry` (`waggledance/application/services/hex_topology_registry.py`) | `HexCellTopology` (class in `hex_cell_topology.py`) |
| **Cell IDs** | `hub`, `bee_ops`, `environment`, `home_comfort`, `safety_security`, `production`, `logistics` | `general`, `thermal`, `energy`, `safety`, `seasonal`, `math`, `system`, `learning` |
| **Adjacency** | Geometric (axial `(q,r)` coordinates from a 7-cell honeycomb) | Hand-coded conceptual (`thermal ↔ energy`, `energy ↔ safety`, etc.) |
| **Consumed by** | `HexNeighborAssist` (chat fallback path, **currently `hex_mesh.enabled=false`** in `configs/settings.yaml`) | `HybridRetrievalService.retrieve(intent, query)` |
| **What it maps** | 75 production agents → cell, then to "expert in domain" prompt | Query intent + keywords → cell, then to per-cell FAISS index |
| **Routing input** | `agent.domain` (per `agents/<id>/core.yaml` header; sourced from `configs/alias_registry.yaml` canonical IDs per audit fix H1+H24) | Free-text query + intent string from `SolverRouter.classify_intent` |
| **Persistence** | Stateless registry (rebuilt at boot) | Stateless topology (constants in code) + per-cell FAISS files in `data/faiss/` if `faiss-cpu` is installed |

---

## Why two topologies

They solve different problems:

1. **Agent routing** (7-cell). When an LLM-only chat fallback fires
   (`HexNeighborAssist.resolve`), the system asks: *which subset of the
   75 agents should provide expertise for this query?* The 7-cell
   layout groups agents by **operational domain** (apiary
   beekeeping, home comfort, factory production, etc.). The cells
   are a hexagonal grid with axial coordinates so the neighbor
   relationships are deterministic.

2. **Solver retrieval** (8-cell). When the hybrid retrieval pipeline
   needs a per-cell FAISS index for fast lookup, it asks: *which
   knowledge cluster should this query search first?* The 8-cell
   layout groups KNOWLEDGE by **subject** (thermal, energy, safety,
   seasonal, math, system, learning, plus a general bucket). Cells
   are not on a grid; adjacency is hand-coded based on conceptual
   overlap (`thermal ↔ energy` because heating affects power).

They do not need to agree on cell IDs because they map different
inputs to different outputs.

---

## How a query flows through both

A `POST /api/chat` request is the only place where both topologies
can fire in the same call. Order:

1. `ChatService.handle` extracts the intent via
   `SolverRouter.classify_intent` (keyword heuristic).
2. **Solver-retrieval topology fires first** (lines 163–167 of
   `chat_service.py`): `HybridRetrievalService.retrieve(intent,
   query)` calls `HexCellTopology.assign_cell(intent, query)` to
   pick one of the 8 cells, searches that cell's FAISS index, then
   optionally ring-1 / ring-2 neighbors.
3. If hybrid retrieval doesn't answer with sufficient confidence,
   **agent-routing topology fires** (lines 172–202): `HexNeighborAssist.resolve(query, intent)` calls
   `HexTopologyRegistry.select_origin_cell` to pick one of the 7
   cells, gets the agents in that cell, builds an "expert in X" LLM
   prompt.

The two topologies never compare their cell IDs. The 8-cell call
produces an "answered" trace; the 7-cell call produces an LLM-
generated response. They are sequential branches in the chat
dispatcher.

---

## Keyword vocabulary drift (audit H14)

For the same query, the two topologies can pick conceptually
different "cells" because their keyword vocabularies were designed
independently:

| Query | 7-cell `HexTopologyRegistry` selector | 8-cell `HexCellTopology._keyword_scan` |
|---|---|---|
| `"talven sähkölasku"` (FI, winter electric bill) | None of the selectors match → falls through to agent-count tiebreaker | `talvi` matches `CELL_SEASONAL`; `sähkö` matches `CELL_ENERGY` |
| `"frost warning"` | `frost` matches `environment` selector | `frost` matches `CELL_THERMAL` keywords |
| `"palovaroitin piippaa"` (FI, smoke alarm beeping) | No match in any 7-cell selector | No match in any 8-cell keyword either (gap noted in audit) |

This drift is **acceptable** as long as readers know the two
topologies route different inputs to different outputs. If a future
PR unifies the keyword vocab, both should change in lockstep.

---

## Common confusion points

- **"How many hex cells does WaggleDance have?"** — Both 7 and 8.
  The right answer is "7 for agent routing, 8 for solver retrieval".
- **"Why do the cell counts differ?"** — Historical: the 8-cell
  topology was designed first for FAISS retrieval; the 7-cell layout
  was added later for agent routing with a geometric layout. They
  converged on different cell counts because their input domains are
  different.
- **"Does the chat flow use both?"** — Yes, sequentially. See "How a
  query flows" above.
- **"Is `hex_mesh.enabled` related to either?"** — That flag gates
  the *7-cell* `HexNeighborAssist` path only. Solver retrieval (8-cell)
  is gated by `hybrid_retrieval.enabled` and `hybrid_retrieval.mode`.

---

## Related audit findings

- **H1, H24, H26, H27, H28**: 7-cell topology had all 75 agents
  collapsing into the `hub` cell because `agent.domain` defaulted to
  `"general"`. Fixed by PR #245 (AliasRegistry-derived domain +
  hex_cells.yaml selector extension).
- **H4**: Two-topology coexistence is itself a finding. This
  document is the response.
- **H14**: Keyword vocabulary drift between the two. Documented in
  the "Keyword vocabulary drift" section above; no code fix because
  the drift is intentional given the different routing inputs.
- **H17**: README internal inconsistency on cell count. Fixed in
  this PR alongside the new doc.
- **H56**: `hex_mesh.enabled=false` in production today, so the
  7-cell topology's downstream (HexNeighborAssist) is dormant.
  When the operator flips the flag, this doc + the H1 fix become
  the orientation material.

---

**Last updated**: 2026-05-11 by PR-E. Update if either topology's
cell count, IDs, or adjacency changes.
