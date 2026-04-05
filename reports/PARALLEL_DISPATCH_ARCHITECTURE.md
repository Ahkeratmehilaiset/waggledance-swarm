# Parallel LLM Dispatch Architecture

## Overview

The Parallel LLM Dispatcher provides bounded concurrent LLM call orchestration
for WaggleDance. It is a feature-flagged, additive layer that sits between
callers (orchestrator, candidate lab, etc.) and the LLM/Gemma infrastructure.

When disabled (`llm_parallel.enabled: false` — the default), all calls pass
through to the underlying adapter with zero overhead.

## Design Principles

1. **Additive only** — old sequential behavior is the default
2. **Solver-first untouched** — parallelism only for genuinely independent LLM calls
3. **No new nondeterministic side effects** on production routing
4. **Bounded concurrency** — global + per-model semaphores prevent resource exhaustion
5. **Graceful degradation** — any failure falls back to sequential

## Architecture

```
                    ┌────────────────────────────────┐
                    │   Caller (Orchestrator, etc.)   │
                    └──────────┬─────────────────────┘
                               │
                    ┌──────────▼─────────────────────┐
                    │   ParallelLLMDispatcher         │
                    │  ┌──────────────────────────┐  │
                    │  │  Global Semaphore (N=4)   │  │
                    │  └──────────────────────────┘  │
                    │  ┌────────┬────────┬────────┐  │
                    │  │ Fast   │ Heavy  │Default │  │
                    │  │ Line   │ Line   │ Line   │  │
                    │  │ (N=2)  │ (N=2)  │ (N=2)  │  │
                    │  └───┬────┴───┬────┴───┬────┘  │
                    │      │        │        │       │
                    │  ┌───▼────────▼────────▼───┐   │
                    │  │   Dedup / Timeout Layer  │   │
                    │  └────────────┬─────────────┘  │
                    └──────────────┬──────────────────┘
                                   │
                    ┌──────────────▼──────────────────┐
                    │   GemmaProfileRouter (optional)  │
                    │   or direct OllamaAdapter        │
                    └──────────────┬──────────────────┘
                                   │
                    ┌──────────────▼──────────────────┐
                    │       Ollama HTTP API             │
                    └──────────────────────────────────┘
```

## Concurrency Model

- **Global semaphore**: Bounds total concurrent LLM calls across all model lines.
  Default: 4 (matches `OLLAMA_NUM_PARALLEL=4` when configured).
- **Per-model semaphores**: Each model line (fast/heavy/default) has its own
  semaphore. Default: 2 per line. This prevents a flood of fast calls from
  starving heavy reasoning calls.
- **Timeout**: Per-request timeout (default 120s) with proper cancellation.
- **Dedup**: SHA-256 of (prompt, model_hint, temperature, max_tokens). If an
  identical request is already in-flight, subsequent callers await the same
  future instead of dispatching a duplicate.

## Model Lines

| Line    | Purpose                               | Typical Model   |
|---------|---------------------------------------|-----------------|
| fast    | Tool/agent/fallback work              | gemma4:e4b      |
| heavy   | Reasoning/candidate-lab/verifier      | gemma4:26b      |
| default | Standard chat/LLM calls (phi4-mini)   | phi4-mini       |

## Integration Points

### HIGH parallelization opportunities (P2 targets)

1. **Orchestrator.run_round_table()** — 6 agent responses in first pass
   - Independent initial responses can be parallelized
   - Phase 2 (refinement) sees previous responses — stays sequential

2. **Orchestrator._execute_agents()** — 6 expert agent calls
   - Each agent processes query independently — can parallelize

3. **SolverCandidateLab.gemma_assisted_analysis()** — 2-5 candidate enrichments
   - Each candidate enrichment is independent — can parallelize

4. **RoundTableEngine.deliberate()** Phase 1 — 6 agent reviews
   - Note: each agent currently sees previous 3 responses
   - For parallel first pass: agents see initial responses only, not other reviews
   - Controlled by `round_table_parallel_first_pass` flag

## Configuration

```yaml
llm_parallel:
  enabled: false                          # Feature flag (safe default)
  max_concurrent: 4                       # Global semaphore
  max_inflight_per_model: 2               # Per-model semaphore
  request_timeout_s: 120                  # Per-request timeout
  round_table_parallel_first_pass: false  # Parallel first RT pass
  dream_batch_parallelism: 1              # Dream/counterfactual batch size
  candidate_lab_parallelism: 1            # Candidate enrichment batch size
  verifier_advisory_parallelism: 1        # Verifier advisory batch size
  dedupe_identical_prompts: true          # Dedup identical concurrent prompts
```

## Metrics

Exposed via `/api/status` and `/api/ops` under `llm_parallel` key:

| Metric                       | Description                          |
|------------------------------|--------------------------------------|
| enabled                      | Feature flag state                   |
| queue_depth                  | Requests waiting for semaphore       |
| inflight_total               | Total active LLM calls               |
| inflight_fast                | Active fast-line calls               |
| inflight_heavy               | Active heavy-line calls              |
| inflight_default             | Active default-line calls            |
| completed_parallel_batches   | Total parallel batches completed     |
| timeout_count                | Requests that timed out              |
| cancelled_count              | Requests that were cancelled         |
| deduped_requests             | Duplicate requests that were deduped |
| degrade_to_sequential_count  | Fallbacks to sequential dispatch     |

## Files

| File | Role |
|------|------|
| `waggledance/application/services/parallel_llm_dispatcher.py` | Core service |
| `waggledance/adapters/config/settings_loader.py` | Settings fields |
| `configs/settings.yaml` | Configuration |
| `waggledance/bootstrap/container.py` | DI wiring |
