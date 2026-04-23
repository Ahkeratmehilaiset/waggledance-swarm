# Hex Topology Performance Metrics — WaggleDance v3.5.7

**Date:** 2026-04-23  
**Scope:** Hex-cell routing, hybrid retrieval, solver scaling, MAGMA auditability, and long-run stability  
**Status:** Metrics specification / implementation target  
**Applies to:** WaggleDance v3.5.7 “Honest Hologram Release” and post-v3.5.7 hardening work

---

## 0. Purpose

This document defines the metrics needed to prove whether WaggleDance’s hexagonal topology actually improves capability, speed, auditability, and scaling — instead of remaining a metaphor.

The target is not to claim “consciousness” as a vague property. The target is to operationalize it as measurable **capability surface growth**:

> More deterministic solver coverage, fewer LLM fallbacks, better verified answers, more useful cross-cell compositions, and complete MAGMA traces — without breaking latency, UI stability, auth, or safety gates.

The metrics below should be implemented before enabling hybrid retrieval as authoritative production routing.

---

## 1. Core Definitions

### 1.1 Hex cell

A semantic routing region in the WaggleDance honeycomb. Current top-level cells include domains such as:

- `thermal`
- `energy`
- `safety`
- `seasonal`
- `math`
- `system`
- `learning`
- `general`

A cell may be:

| State | Meaning |
|---|---|
| `non_empty` | Contains one or more solver/axiom documents in the local FAISS index |
| `empty` | Valid semantic target but no local solver coverage yet |
| `bridge` | Cell or solver path that composes two or more neighboring cells |
| `stale` | Index exists but backfill/hash/version is not current |
| `disabled` | Cell intentionally excluded from routing |

### 1.2 Retrieval modes

Hybrid retrieval must support these modes:

| Mode | Behavior |
|---|---|
| `shadow` | Hybrid computes candidates but production route remains unchanged |
| `candidate` | Hybrid candidate is visible to verifier/router but old route can override |
| `authoritative` | Hybrid retrieval chooses production solver path |

### 1.3 Fallback stages

Every route must record the first successful stage:

1. `local_cell_faiss`
2. `ring1_faiss`
3. `ring2_faiss`
4. `global_chroma`
5. `llm_fallback`
6. `no_answer`

---

## 2. Topology Coverage Metrics

These measure whether the hex map is populated and balanced.

| Metric | Type | Meaning | Target |
|---|---:|---|---|
| `hex.cells_total` | gauge | Total known cells | grows only by plan/subdivision |
| `hex.cells_non_empty` | gauge | Cells with at least one solver document | increasing |
| `hex.empty_cell_count` | gauge | Cells with zero solvers | decreasing |
| `hex.cell_solver_count{cell}` | gauge | Solver docs per cell | no critical cell = 0 |
| `hex.cell_occupancy_ratio` | gauge | `non_empty / total` | ≥ 0.75 before authoritative |
| `hex.cell_balance_entropy` | gauge | Shannon entropy over solver distribution | increasing |
| `hex.cell_gini` | gauge | Imbalance of solver distribution | decreasing |
| `hex.empty_cell_miss_total{cell}` | counter | Queries semantically routed to empty cell | should create backlog, not errors |
| `hex.empty_cell_fallback_success_rate` | gauge | Empty-cell misses rescued by ring/global solver | ≥ 0.80 |

### Formula: cell balance entropy

```text
p_i = solver_count_i / total_solver_count

entropy = -Σ p_i log(p_i)
normalized_entropy = entropy / log(non_empty_cell_count)
```

High normalized entropy means the solver library is not collapsing into one giant `general` cell.

---

## 3. Routing Honesty Metrics

These prove whether routing is correct, not just fast.

| Metric | Type | Meaning | Target |
|---|---:|---|---|
| `hex.keyword_cell_total{cell}` | counter | Keyword/router-assigned first cell | observe distribution |
| `hex.centroid_cell_top1_total{cell}` | counter | Embedding centroid top-1 cell | observe distribution |
| `hex.keyword_centroid_disagreement_total` | counter | Keyword cell != centroid top-1 | not automatically bad |
| `hex.keyword_centroid_disagreement_rate` | gauge | disagreement / requests | track by domain |
| `hex.oracle_precision_at_1` | gauge | Top-1 solver is correct on oracle set | ≥ keyword baseline - 2pp |
| `hex.oracle_recall_at_5` | gauge | Correct solver appears in top-5 | ≥ keyword baseline + 5pp |
| `hex.false_solver_activation_rate` | gauge | Solver chosen when no solver should answer | ≤ 1% |
| `hex.false_llm_fallback_rate` | gauge | LLM used when solver should answer | decreasing, target -5pp |
| `hex.route_agreement_with_keyword` | gauge | Hybrid route equals old route | migration-risk metric only |
| `hex.hybrid_unique_correct_rate` | gauge | Hybrid differs from keyword and oracle says hybrid correct | increasing |

### Important rule

Keyword agreement is **not** correctness. It is only migration-risk evidence.

Correctness must come from:

1. Axiom oracle set
2. Verified GOLD/SILVER CaseTrajectory set
3. Blind adjudication set

---

## 4. Retrieval Latency Metrics

These prove the honeycomb is fast enough.

| Metric | Type | Meaning | Target |
|---|---:|---|---|
| `hex.route_latency_ms.p50` | gauge | End-to-end routing median | baseline + ≤ 5ms |
| `hex.route_latency_ms.p95` | gauge | End-to-end routing p95 | baseline + ≤ 25ms |
| `hex.embedding_latency_ms.p95` | gauge | Query embedding p95 | stable |
| `hex.faiss_local_latency_ms.p95` | gauge | Local cell FAISS p95 | ≤ 10ms |
| `hex.faiss_ring_latency_ms.p95` | gauge | Ring search p95 | ≤ 25ms |
| `hex.chroma_global_latency_ms.p95` | gauge | Global Chroma fallback p95 | observe |
| `hex.solver_execution_latency_ms.p95` | gauge | Selected solver runtime p95 | per solver SLA |
| `hex.llm_fallback_latency_ms.p95` | gauge | LLM fallback p95 | should be rare |
| `hex.routing_overhead_ms.p95` | gauge | Hybrid overhead above old route | ≤ 25ms |

### Latency budget

For solver-first routing to stay valuable:

```text
embedding + cell centroid + local/ring FAISS + solver selection <= 25ms p95 target
```

LLM latency is measured separately because LLM should be fallback, not the core path.

---

## 5. Ring Expansion Metrics

These prove that hex topology is doing useful local-neighbor search.

| Metric | Type | Meaning | Target |
|---|---:|---|---|
| `hex.local_hit_rate` | gauge | Answer found in initial cell | high but not forced |
| `hex.ring1_hit_rate` | gauge | Answer found in neighbor cells | useful bridge signal |
| `hex.ring2_hit_rate` | gauge | Answer found in second ring | should be lower |
| `hex.global_hit_rate` | gauge | Needed global fallback | decreasing |
| `hex.llm_fallback_rate` | gauge | Needed LLM fallback | decreasing |
| `hex.ring_expansion_depth_avg` | gauge | Mean rings searched | stable |
| `hex.overexpansion_rate` | gauge | Search expanded despite high local score | low |
| `hex.underexpansion_rate` | gauge | Local low score but ring not searched | zero |

### Interpretation

A healthy honeycomb does not mean “everything is local.”  
It means:

```text
easy queries -> local cell
cross-domain queries -> ring1 bridge
rare/uncovered queries -> global/LLM fallback
```

---

## 6. Composite Intelligence Metrics

These measure “capability growth” without using vague claims.

| Metric | Type | Meaning | Target |
|---|---:|---|---|
| `hex.composite_paths_total` | gauge | Known verified multi-solver paths | increasing |
| `hex.useful_composite_paths_total` | gauge | Composite paths that improved verified outcome | increasing |
| `hex.bridge_solver_count{from,to}` | gauge | Solvers bridging adjacent cells | increasing |
| `hex.bridge_success_total{from,to}` | counter | Bridge route verified success | increasing |
| `hex.bridge_failure_total{from,to}` | counter | Bridge route failed verifier | low |
| `hex.composition_precision` | gauge | Verified composite / attempted composite | ≥ 0.90 |
| `hex.composition_uplift` | gauge | Improvement vs best single solver | positive |
| `hex.dream_bridge_candidates_total` | counter | Dream Mode proposed bridge candidates | observe |
| `hex.dream_bridge_promoted_total` | counter | Bridge candidates promoted by gates | increasing carefully |

### Formula: useful composite growth

```text
useful_composite_path =
  route uses >= 2 solvers from >= 2 cells
  AND verifier_pass = true
  AND outcome_score > max(single_solver_scores) + epsilon
```

This is the most honest metric for “exponential” honeycomb intelligence.

---

## 7. Capability Surface Index

A single high-level score for dashboard use.

```text
CapabilitySurfaceIndex =
  cell_occupancy_ratio
  * oracle_recall_at_5
  * verifier_pass_rate
  * (1 - false_solver_activation_rate)
  * (1 - llm_fallback_rate)
  * log1p(useful_composite_paths_total)
  * magma_trace_completeness
```

Use as a trend, not as an absolute scientific value.

### Suggested labels

| Score Trend | Meaning |
|---|---|
| rising CSI + stable latency | healthy capability growth |
| rising CSI + rising false activation | over-routing, unsafe |
| rising solver count + flat CSI | solver bloat |
| rising composite paths + low verifier pass | hallucinated composition |
| falling LLM fallback + stable precision | deterministic coverage improving |

---

## 8. MAGMA / Audit Metrics

Hybrid retrieval must strengthen, not weaken, MAGMA.

| Metric | Type | Meaning | Target |
|---|---:|---|---|
| `magma.retrieval_trace_total` | counter | Retrieval traces written | equals routed requests |
| `magma.retrieval_trace_complete_rate` | gauge | Required fields present | 100% |
| `magma.retrieval_trace_hash_mismatch_total` | counter | Trace integrity failures | 0 |
| `magma.append_only_violation_total` | counter | Audit append-only broken | 0 |
| `magma.untraced_solver_activation_total` | counter | Solver used without trace | 0 |
| `magma.faiss_version_missing_total` | counter | Missing index version in trace | 0 |
| `magma.routing_disagreement_logged_rate` | gauge | Keyword/centroid disagreement traced | 100% |

### Required retrieval trace fields

Every routed query must record:

```yaml
retrieval_trace:
  trace_id: string
  query_hash: string
  timestamp_utc: string
  route_mode: shadow|candidate|authoritative
  embedding_model: string
  embedding_dim: int
  keyword_cell: string
  centroid_cells_top3:
    - cell: string
      score: float
  searched_cells:
    - cell: string
      ring_depth: int
      non_empty: bool
  candidate_doc_ids:
    - string
  chosen_doc_id: string|null
  chosen_solver_id: string|null
  chosen_score: float|null
  fallback_stage: local_cell_faiss|ring1_faiss|ring2_faiss|global_chroma|llm_fallback|no_answer
  faiss_index_version: string
  empty_cell_miss: bool
  keyword_centroid_disagreement: bool
  verifier_result: pass|fail|not_run
  quality_tier: GOLD|SILVER|BRONZE|QUARANTINE|null
```

---

## 9. FAISS Index Safety Metrics

These prevent race conditions and silent corruption.

| Metric | Type | Meaning | Target |
|---|---:|---|---|
| `hex.faiss_index_version{cell}` | gauge/string | Current live index version | present for all non-empty cells |
| `hex.faiss_staging_build_total{cell}` | counter | Staging builds | observe |
| `hex.faiss_atomic_swap_total{cell}` | counter | Successful live swaps | observe |
| `hex.faiss_swap_failure_total{cell}` | counter | Failed swaps | 0 |
| `hex.faiss_version_mismatch_total` | counter | Query saw inconsistent version | 0 |
| `hex.faiss_write_lock_wait_ms.p95` | gauge | Writer lock wait | low |
| `hex.faiss_read_lock_wait_ms.p95` | gauge | Reader lock wait | low |
| `hex.faiss_read_during_write_total` | counter | Unsafe read/write overlap | 0 |

### Required invariant

```text
Backfill writes staging index.
Validation reads staging index.
Production reads only live immutable snapshot.
Swap is atomic.
Every trace records index version.
```

---

## 10. Multilingual Symmetry Metrics

These prove Finnish/English routing does not diverge.

| Metric | Type | Meaning | Target |
|---|---:|---|---|
| `hex.oracle_recall_at_5{lang=fi}` | gauge | Finnish recall | within 5pp of English |
| `hex.oracle_recall_at_5{lang=en}` | gauge | English recall | baseline |
| `hex.oracle_recall_at_5{lang=mixed}` | gauge | Mixed-language recall | ≥ 80% |
| `hex.negation_failure_total{lang}` | counter | Negation handled incorrectly | 0 on oracle |
| `hex.threshold_query_success_rate{lang}` | gauge | “is cost > 50€” style queries | ≥ 95% on oracle |
| `hex.alias_view_hit_rate{view_type}` | gauge | Which embedding view matched | observe |

### Required embedding views

Each solver should have multiple indexed views:

```yaml
views:
  - type: canonical_en
  - type: canonical_fi
  - type: synonym_mixed
  - type: unit_variable
  - type: example_queries
```

Top-k results must be deduplicated by `canonical_solver_id`.

---

## 11. Negation / Question-Type Metrics

Embedding retrieval alone cannot understand answer shape.

| Metric | Type | Meaning | Target |
|---|---:|---|---|
| `hex.question_frame_detected_rate` | gauge | Parser found answer type | ≥ 95% on oracle |
| `hex.boolean_threshold_success_rate` | gauge | Correct yes/no comparison | ≥ 95% |
| `hex.numeric_answer_success_rate` | gauge | Correct numeric solver use | ≥ 95% |
| `hex.explanation_answer_success_rate` | gauge | Numeric solver + explanation | ≥ 90% |
| `hex.diagnosis_route_success_rate` | gauge | Diagnosis routed correctly | increasing |
| `hex.negation_scope_error_total` | counter | Negation scope wrong | 0 on oracle |

Question frame:

```yaml
question_frame:
  desired_output: numeric|boolean_comparison|explanation|diagnosis|optimization
  comparator:
    op: ">"|"<"|">="|"<="|"="|null
    threshold: number|null
    unit: string|null
  negation:
    present: bool
    scope: string|null
```

---

## 12. Learning and Promotion Metrics

These measure whether the honeycomb improves safely.

| Metric | Type | Meaning | Target |
|---|---:|---|---|
| `learning.open_gap_score{cell}` | gauge | How badly cell needs new solvers | prioritized |
| `learning.solver_proposals_total{cell}` | counter | Claude/Dream proposals | observe |
| `learning.solver_duplicates_rejected_total` | counter | Hash dedupe rejections | expected |
| `learning.solver_schema_fail_total` | counter | Invalid proposals | decreasing |
| `learning.solver_contradiction_total` | counter | Contradiction gate rejects | low |
| `learning.solver_promoted_total{cell}` | counter | Accepted new solvers | careful growth |
| `learning.solver_rollback_total{cell}` | counter | Canary rollback | low |
| `learning.quality_tier_total{tier}` | counter | GOLD/SILVER/BRONZE/QUARANTINE | monitor drift |
| `learning.dream_insight_uplift` | gauge | Dream-generated improvement | positive |

Promotion gate:

```text
proposal
 -> schema
 -> deterministic replay
 -> contradiction check
 -> oracle tests
 -> verifier
 -> shadow
 -> canary
 -> promotion
```

---

## 13. Stability Metrics for 24h / 400h Runs

These prove the topology survives long campaigns.

| Metric | Type | Target |
|---|---:|---|
| `run.health_200_rate` | gauge | 100% |
| `run.ready_200_rate` | gauge | 100% |
| `run.auth_regression_total` | counter | 0 |
| `run.xss_execution_total` | counter | 0 |
| `run.dom_break_total` | counter | 0 |
| `run.console_error_total` | counter | 0 critical |
| `run.failed_request_total` | counter | 0 unexpected |
| `run.backend_5xx_total` | counter | 0 or below baseline |
| `run.queue_full_total` | counter | 0 |
| `run.uvicorn_restart_total` | counter | 0 |
| `run.memory_growth_pct` | gauge | ≤ 15% over baseline |
| `run.context_recycle_total` | counter | expected in UI gauntlet |
| `run.segment_green_hours{segment}` | gauge | continuous |
| `run.checkpoint_pass_total` | counter | every checkpoint |

400h segmentation:

| Segment | Purpose |
|---|---|
| HOT | high-volume chat / query gauntlet |
| WARM | mixed UI cycles, tabs, feeds, auth |
| COLD | health/ready/cookie/session liveness |
| FAULT | controlled bad tokens, bad bodies, restarts, degraded LLM |
| LEARNING | Dream/quality gate/shadow promotion validation |
| RETRIEVAL | hybrid routing oracle and live shadow validation |

---

## 14. Prometheus Metric Names

Recommended concrete names:

```text
waggledance_hex_cells_total
waggledance_hex_cells_non_empty
waggledance_hex_cell_solver_count{cell}
waggledance_hex_cell_occupancy_ratio
waggledance_hex_cell_balance_entropy
waggledance_hex_cell_gini

waggledance_hex_route_requests_total{mode,fallback_stage}
waggledance_hex_keyword_centroid_disagreement_total
waggledance_hex_empty_cell_miss_total{cell}
waggledance_hex_oracle_precision_at_1
waggledance_hex_oracle_recall_at_5
waggledance_hex_false_solver_activation_rate
waggledance_hex_false_llm_fallback_rate

waggledance_hex_route_latency_seconds_bucket
waggledance_hex_embedding_latency_seconds_bucket
waggledance_hex_faiss_local_latency_seconds_bucket
waggledance_hex_faiss_ring_latency_seconds_bucket
waggledance_hex_solver_latency_seconds_bucket

waggledance_hex_local_hit_total
waggledance_hex_ring1_hit_total
waggledance_hex_ring2_hit_total
waggledance_hex_global_hit_total
waggledance_hex_llm_fallback_total

waggledance_hex_composite_paths_total
waggledance_hex_useful_composite_paths_total
waggledance_hex_bridge_success_total{from_cell,to_cell}
waggledance_hex_bridge_failure_total{from_cell,to_cell}

waggledance_magma_retrieval_trace_total
waggledance_magma_retrieval_trace_complete_rate
waggledance_magma_untraced_solver_activation_total
waggledance_magma_append_only_violation_total

waggledance_faiss_index_version{cell}
waggledance_faiss_atomic_swap_total{cell}
waggledance_faiss_swap_failure_total{cell}
waggledance_faiss_version_mismatch_total
waggledance_faiss_read_during_write_total
```

---

## 15. Rollback / Kill Signals

Hybrid retrieval must roll back from `authoritative` to `candidate` or `shadow` if any of these trigger.

| Kill Signal | Threshold |
|---|---:|
| false solver activation | > 1% |
| LLM fallback increase | > 3pp for 3 consecutive windows |
| p95 routing latency | > baseline + 25ms for 3 consecutive windows |
| embedding timeout rate | > 2% |
| backend 5xx | > baseline + 0.5pp |
| auth/XSS/DOM regression | any confirmed regression |
| memory growth | > 15% over baseline |
| FAISS version mismatch | any |
| untraced solver activation | any |
| MAGMA append-only violation | any |
| empty-cell fallback failure | sustained > 20% |

Window size:

```text
minimum 100 routed queries
or 10 minutes, whichever is larger
```

---

## 16. Dashboard Layout

Add a “Hex Topology” dashboard panel with these cards:

1. **Topology Health**
   - cells total
   - non-empty cells
   - empty cells
   - entropy/gini

2. **Routing Honesty**
   - oracle precision@1
   - oracle recall@5
   - false solver activation
   - false LLM fallback

3. **Ring Flow**
   - local/ring1/ring2/global/LLM distribution
   - top bridge pairs

4. **Composite Intelligence**
   - useful composite paths
   - bridge uplift
   - verifier pass rate

5. **FAISS Safety**
   - live index versions
   - swaps
   - mismatch count
   - read/write overlap count

6. **MAGMA Integrity**
   - trace completeness
   - untraced activation
   - append-only violations

7. **Long Run**
   - green hours
   - memory growth
   - auth/XSS/DOM regressions
   - queue-full / restart counts

---

## 17. Activation Gate

Hybrid retrieval can move from `shadow` to `candidate` when:

```text
- axiom backfill complete
- all non-empty cells have live FAISS index version
- MAGMA retrieval trace completeness = 100%
- oracle set exists for every solver family
- 0 FAISS version mismatches
- 0 untraced solver activations
```

Hybrid retrieval can move from `candidate` to `authoritative` when:

```text
- oracle precision@1 >= keyword baseline - 2pp
- oracle recall@5 >= keyword baseline + 5pp
- false solver activation <= 1%
- false LLM fallback decreases
- p95 route latency <= baseline + 25ms
- 24h shadow/candidate run has 0 critical regressions
- auth/XSS/DOM remain green
- MAGMA trace complete rate = 100%
```

400h campaign counts as “scale proof” only if:

```text
- HOT/WARM/COLD segments stay green
- no hidden server restarts
- no FAISS version mismatch
- no MAGMA append-only violation
- no safety/auth regression
- capability surface index trends upward
```

---

## 18. Summary

The hex topology becomes real when these are true:

1. Cells are populated and balanced.
2. Empty cells become honest gap signals, not silent failures.
3. Keyword and embedding routing are compared, not confused.
4. Correctness comes from oracle/verifier truth, not keyword agreement.
5. FAISS updates are versioned and atomic.
6. Every retrieval is MAGMA-traced.
7. Solver composition improves verified outcomes.
8. LLM fallback decreases without increasing false solver activation.
9. Long-run HOT/WARM/COLD campaigns stay green.
10. Capability Surface Index rises while latency and safety remain stable.

If these metrics are implemented, WaggleDance can honestly claim:

> One stable v3.5.7 node is proven.  
> The next step is a measured, auditable honeycomb of solvers — not hype, but measurable topology-driven capability growth.
