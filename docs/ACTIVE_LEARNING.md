# Active Learning

WaggleDance uses active learning to prioritize which queries and corrections provide the most training value.

## Priority Scoring

The `ActiveLearningScorer` (in `core/active_learning.py`) evaluates each candidate using four signals:

| Signal | Weight | Description |
|--------|--------|-------------|
| Low confidence | 0.35 | LLM confidence < 0.6 — uncertain answers need more training data |
| Topic gap | 0.25 | Few training examples for this topic — under-represented domains |
| User correction | 0.25 | User corrected the answer — direct quality signal |
| Repeated fallback | 0.15 | Query fell back to LLM multiple times — routing failure |

The combined score (0.0-1.0) determines training priority. Higher-scored candidates are selected first for micromodel training and knowledge enrichment.

## Canary Promotion

Before any learned improvement goes live, it passes through the `CanaryPromoter` (`core/canary_promoter.py`):

1. **Start canary** — register experiment with baseline score
2. **Collect samples** — evaluate against real queries
3. **Evaluate** — check if improvement > min_improvement threshold
4. **Promote or rollback** — auto-rollback if performance drops

Configuration: `min_samples=50`, `min_improvement=0.05`, `enabled=true`

## Learning Ledger

All learning events are recorded in `core/learning_ledger.py` as append-only JSONL:

- `fact_stored` — new fact added to memory
- `correction` — user corrected an answer
- `promotion` — canary promoted to production
- `canary` — canary experiment started/evaluated
- `rollback` — promotion rolled back

Query events: `ledger.query(event_type="correction", since=timestamp, limit=100)`
