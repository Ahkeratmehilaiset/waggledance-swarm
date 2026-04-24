# Hex subdivision plan

- **Generated:** 2026-04-24T04:44:43+00:00

## Thresholds in effect

| trigger | threshold |
|---|---|
| `solver_count` | 30 |
| `gap_score` | 0.7 |
| `fallback_rate` | 0.5 |
| `entropy` | 6 |
| `bridges` | 6 |
| `rejections` | 3 |

## Candidates

### `seasonal` — severity 5.5

Triggers:
- `output_unit_entropy` observed=1 threshold=1 — cell hosts many distinct output units — likely heterogeneous
- `bridge_anchor_count` observed=27 threshold=6 — many valid bridge candidates anchor on this cell

**Proposed sub-cells:** `seasonal.core`, `seasonal.advanced`, `seasonal.seasonal`

**Expected benefit:** routing precision improves once queries hit a sub-cell matching their topic, and the teacher's per-manifest context shrinks.
**Risk:** sub-cell adjacency must be wired correctly; a bad split degrades ring-1 reachability and may starve some solvers of neighbor assistance.
**Rollback plan:** revert the hex_cell_topology.py commit. Solvers in sub-cells are re-tagged back to parent cell_id in their YAML and the registry reload picks it up on next server start.

**Tests needed:**
- adjacency and ring-2 match expected sub-cells
- every existing solver in the parent maps cleanly to exactly one sub-cell
- routing accuracy on the cell's oracle set is >= pre-subdivision baseline
- LLM fallback rate on the cell does not regress

### `learning` — severity 3.5

Triggers:
- `output_unit_entropy` observed=1 threshold=1 — cell hosts many distinct output units — likely heterogeneous
- `bridge_anchor_count` observed=15 threshold=6 — many valid bridge candidates anchor on this cell

**Proposed sub-cells:** `learning.core`, `learning.advanced`, `learning.seasonal`

**Expected benefit:** routing precision improves once queries hit a sub-cell matching their topic, and the teacher's per-manifest context shrinks.
**Risk:** sub-cell adjacency must be wired correctly; a bad split degrades ring-1 reachability and may starve some solvers of neighbor assistance.
**Rollback plan:** revert the hex_cell_topology.py commit. Solvers in sub-cells are re-tagged back to parent cell_id in their YAML and the registry reload picks it up on next server start.

**Tests needed:**
- adjacency and ring-2 match expected sub-cells
- every existing solver in the parent maps cleanly to exactly one sub-cell
- routing accuracy on the cell's oracle set is >= pre-subdivision baseline
- LLM fallback rate on the cell does not regress

### `system` — severity 3.5

Triggers:
- `output_unit_entropy` observed=1 threshold=1 — cell hosts many distinct output units — likely heterogeneous
- `bridge_anchor_count` observed=15 threshold=6 — many valid bridge candidates anchor on this cell

**Proposed sub-cells:** `system.core`, `system.advanced`, `system.seasonal`

**Expected benefit:** routing precision improves once queries hit a sub-cell matching their topic, and the teacher's per-manifest context shrinks.
**Risk:** sub-cell adjacency must be wired correctly; a bad split degrades ring-1 reachability and may starve some solvers of neighbor assistance.
**Rollback plan:** revert the hex_cell_topology.py commit. Solvers in sub-cells are re-tagged back to parent cell_id in their YAML and the registry reload picks it up on next server start.

**Tests needed:**
- adjacency and ring-2 match expected sub-cells
- every existing solver in the parent maps cleanly to exactly one sub-cell
- routing accuracy on the cell's oracle set is >= pre-subdivision baseline
- LLM fallback rate on the cell does not regress

### `general` — severity 2.333

Triggers:
- `bridge_anchor_count` observed=14 threshold=6 — many valid bridge candidates anchor on this cell

**Proposed sub-cells:** `general.core`, `general.advanced`, `general.seasonal`

**Expected benefit:** routing precision improves once queries hit a sub-cell matching their topic, and the teacher's per-manifest context shrinks.
**Risk:** sub-cell adjacency must be wired correctly; a bad split degrades ring-1 reachability and may starve some solvers of neighbor assistance.
**Rollback plan:** revert the hex_cell_topology.py commit. Solvers in sub-cells are re-tagged back to parent cell_id in their YAML and the registry reload picks it up on next server start.

**Tests needed:**
- adjacency and ring-2 match expected sub-cells
- every existing solver in the parent maps cleanly to exactly one sub-cell
- routing accuracy on the cell's oracle set is >= pre-subdivision baseline
- LLM fallback rate on the cell does not regress

### `energy` — severity 1.0

Triggers:
- `output_unit_entropy` observed=1 threshold=1 — cell hosts many distinct output units — likely heterogeneous

**Proposed sub-cells:** `energy.core`, `energy.advanced`, `energy.seasonal`

**Expected benefit:** routing precision improves once queries hit a sub-cell matching their topic, and the teacher's per-manifest context shrinks.
**Risk:** sub-cell adjacency must be wired correctly; a bad split degrades ring-1 reachability and may starve some solvers of neighbor assistance.
**Rollback plan:** revert the hex_cell_topology.py commit. Solvers in sub-cells are re-tagged back to parent cell_id in their YAML and the registry reload picks it up on next server start.

**Tests needed:**
- adjacency and ring-2 match expected sub-cells
- every existing solver in the parent maps cleanly to exactly one sub-cell
- routing accuracy on the cell's oracle set is >= pre-subdivision baseline
- LLM fallback rate on the cell does not regress

### `safety` — severity 1.0

Triggers:
- `output_unit_entropy` observed=1 threshold=1 — cell hosts many distinct output units — likely heterogeneous

**Proposed sub-cells:** `safety.core`, `safety.advanced`, `safety.seasonal`

**Expected benefit:** routing precision improves once queries hit a sub-cell matching their topic, and the teacher's per-manifest context shrinks.
**Risk:** sub-cell adjacency must be wired correctly; a bad split degrades ring-1 reachability and may starve some solvers of neighbor assistance.
**Rollback plan:** revert the hex_cell_topology.py commit. Solvers in sub-cells are re-tagged back to parent cell_id in their YAML and the registry reload picks it up on next server start.

**Tests needed:**
- adjacency and ring-2 match expected sub-cells
- every existing solver in the parent maps cleanly to exactly one sub-cell
- routing accuracy on the cell's oracle set is >= pre-subdivision baseline
- LLM fallback rate on the cell does not regress

### `thermal` — severity 1.0

Triggers:
- `output_unit_entropy` observed=1 threshold=1 — cell hosts many distinct output units — likely heterogeneous

**Proposed sub-cells:** `thermal.core`, `thermal.advanced`, `thermal.seasonal`

**Expected benefit:** routing precision improves once queries hit a sub-cell matching their topic, and the teacher's per-manifest context shrinks.
**Risk:** sub-cell adjacency must be wired correctly; a bad split degrades ring-1 reachability and may starve some solvers of neighbor assistance.
**Rollback plan:** revert the hex_cell_topology.py commit. Solvers in sub-cells are re-tagged back to parent cell_id in their YAML and the registry reload picks it up on next server start.

**Tests needed:**
- adjacency and ring-2 match expected sub-cells
- every existing solver in the parent maps cleanly to exactly one sub-cell
- routing accuracy on the cell's oracle set is >= pre-subdivision baseline
- LLM fallback rate on the cell does not regress


## Reference data

### Cells anchoring bridge candidates

- `seasonal`: 27
- `learning`: 15
- `system`: 15
- `general`: 14
- `math`: 2
- `safety`: 2

### Cells flagged for high output-unit entropy

- `energy`
- `learning`
- `safety`
- `seasonal`
- `system`
- `thermal`


> **Important:** This tool writes a document, not a topology change. Moving from flat cells to sub-cells requires a separate reviewed commit against `waggledance/core/hex_cell_topology.py` plus the tests listed per candidate.
