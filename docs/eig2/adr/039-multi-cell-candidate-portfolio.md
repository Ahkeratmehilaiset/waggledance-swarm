# ADR-039 — Multi-cell candidate set (portfolio routing)

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-038 (tunnel overlay)

## Context

`HexTopologyRegistry.select_origin_cell()` returns a single cell. Best-of-K isn't possible because there is no K. A query that semantically belongs to two cells (e.g., "factory thermal regulation" → both `factory` and `thermal`) currently picks ONE and routes; the other cell's solvers never see the query.

The 50-leaps menu (L4) calls for **multi-cell candidate set**: return top-K cells with confidence weights. Downstream verifier picks. Best-of-K answer instead of single point-of-failure.

## Decision

`HexTopologyRegistry.select_origin_cells_top_k(query, k=3)` returns a list of (cell_id, confidence) tuples sorted by descending confidence:

```python
@dataclass(frozen=True, slots=True)
class CellCandidate:
    cell_id: str
    confidence: float  # 0.0-1.0
    matched_selectors: tuple[str, ...]  # for audit / debug

def select_origin_cells_top_k(query: str, k: int = 3) -> list[CellCandidate]: ...
```

Default `k=3` (good top-3 vs single point). Profile-tunable.

The existing `select_origin_cell(query)` remains as a convenience returning the first element's cell_id for backward compat. NO breaking change.

## Consequences

### Routing intelligence

* Multi-cell queries route to a portfolio: downstream verifier (or weighted ensemble) picks final answer.
* Single-cell queries still produce K=1 result with confidence near 1.0; behavior is gracefully smaller.

### Storage / memory

* No new persistent state. Top-K computed at query time.
* Cost: same scan as today (O(cells)) + top-K heap (O(cells * log K)). Negligible at current cell count.

### Operational

* `matched_selectors` field aids debugging: operator can see WHICH selectors matched for each candidate.

## Invariants

Pinned in `docs/eig2/contracts/multi_cell_portfolio.json` and verified by `tests/contracts/test_multi_cell_portfolio.py`.

1. **K default 3.** `select_origin_cells_top_k(k=3)`. Range [1, 10].
2. **Sorted descending.** Result list sorted by confidence DESC. First element matches `select_origin_cell()` legacy return.
3. **Confidence in [0, 1].** Each CellCandidate.confidence clamped to [0.0, 1.0].
4. **Matched selectors transparent.** Each candidate has `matched_selectors` tuple listing exact selector strings that matched the query.
5. **Slots dataclass.** CellCandidate is `@dataclass(frozen=True, slots=True)` per L60-NEW pattern.
6. **Backward compat.** Existing `select_origin_cell(query)` remains and returns `select_origin_cells_top_k(query, k=1)[0].cell_id` (or None if no match).
7. **Empty result handling.** When no cell matches, return EMPTY LIST. Caller distinguishes empty vs None semantically.

## Out of scope (this ADR)

* Implementation — separate PR.
* Verifier / ensemble logic that consumes the portfolio — separate ADR.
* Cross-portfolio caching for repeat queries — separate ADR.

## References

* ADR-038 (tunnel overlay, complementary routing-intelligence layer)
* L34 hot-path budget contract (select_origin_cell budget; top_k must stay within profile budget)
* 50-leaps menu: L2, L3, L4 (this), L5
