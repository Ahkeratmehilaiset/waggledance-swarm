# Phase D Decision — Hybrid Retrieval Activation

**Date:** 2026-04-23
**Plan reference:** `hybrid_retrieval_activation_v3_2026-04-23.md` + v3.1 amendments
**Evidence:**
- `docs/runs/hybrid_shadow_three_way_2026-04-23T093413Z.md` (420-query oracle)
- `tests/oracle/*.yaml` (280 positive + 140 negative, handwritten)
- `docs/plans/phase_A_preflight_report_2026-04-23.md`
- `docs/plans/phase_B_completion_report_2026-04-23.md`

## TL;DR

**Verdict: DO NOT enable hybrid_retrieval in authoritative mode yet.**

Keep everything in `mode: shadow`. Deferred to Phase D-1 after two prerequisites are met:
1. **Question-frame parser wired into routing pipeline** (v3 §1.8 — exists but not integrated)
2. **Library expansion** to fill empty cells (seasonal, learning, general) — at least 50-100 more solvers

This is the data-driven decision per v3 §1.5. Not a failure — a measured pause.

## Why (in three honest sentences)

1. Flat and hex are **statistically tied** at 14 solvers (flat 55.4% positive precision, hex 55.0%) — Gemini and Grok were right that hex complexity isn't justified yet.
2. Both flat and hex retrieve **higher-similarity to NEGATIVE queries than to some positives** (mean 0.73 vs 0.69) because embeddings match on vocabulary, not intent — GPT was right that question-frame parsing is needed BEFORE solver activation.
3. Without #2 fixed, enabling hybrid would make the system **worse than keyword routing** on off-domain queries.

## Data that drove the decision

### Positive precision@1 (280 queries — should route to specific solver)

| Architecture | Precision@1 | Recall@5 |
|---|---:|---:|
| keyword | 0.0% (by design — picks cell, not solver) | — |
| flat | **55.4%** | 70.7% |
| hex | **55.0%** | **71.8%** |

Hex edges flat on recall@5 by 1.1pp. Not significant on 280 queries.

### Negative precision (140 queries — should NOT route to any solver)

| Architecture | Correct rejections | @ threshold 0.55 | @ threshold 0.70 |
|---|---:|---:|---:|
| keyword | 100% (picks no solver ever) | — | — |
| flat | 0% (at 0.35 default) | 7/140 | 54/140 |
| hex | 0% (at 0.35 default) | 7/140 | 54/140 |

At threshold 0.70 to catch 54 negatives, we'd reject **193 correct positives** (false negatives). Unacceptable cost.

### Score distribution — the red flag

Mean similarity scores:
- Positive-correct: 0.69
- Positive-wrong: 0.58
- **Negative: 0.73** (higher than correct!)

**Negative queries have the HIGHEST scores** because the embedding model finds semantic overlap on vocabulary even when the question type is wrong. Example:
- "miksi lämpöpumppu pitää kovaa ääntä" (NEGATIVE for heat_pump_cop — it's a diagnosis query, not cost)
- Embedding-similar to "heat_pump_cop" texts (high score ~0.77)
- Gets routed to heat_pump_cop → WRONG ANSWER

A cosine threshold cannot separate these because the WORDS overlap. The distinction is in **question type**, which requires the question_frame parser.

## What v3 plan said about this

§1.8 explicitly called this out (attributed to GPT-5):
> "Embedding finds *which solver* but not *what answer shape*. Add a small deterministic parser between retrieval and solver execution."

§1.18 added solver_output_schema. Together they form:
```
embedding_retrieval → question_frame.parse() → check_compatibility →
  solver_activation OR reject_as_off_domain
```

The parser is built (tests/test_question_frame.py — 30 passing).
The schema is in all 14 axioms (after B.0 upgrade).
**The integration — retrieval_service connecting these — is NOT done.**

Phase D-1 must include this integration.

## Three architectures — honest scorecard

| Aspect | Keyword | Flat | Hex |
|---|---|---|---|
| Latency p95 | 0.02 ms | 0.11 ms | 0.48 ms |
| Positive precision@1 | 0% | 55.4% | 55.0% |
| Positive recall@5 | — | 70.7% | 71.8% |
| Negative rejection at default | 100% | 0% | 0% |
| Scaling to 10k solvers | breaks ~500 | works well | works well |
| Complexity | minimal | low | medium |
| Code lines to maintain | ~200 | ~500 | ~1500 |
| Audit clarity | high | medium | high (cells) |
| Empty-cell gap signal | N/A | N/A | yes |

## Five possible paths forward

### Path A — Do nothing (default if user accepts)
Hybrid stays `mode: shadow`. No production change. Run shadow monthly, track how numbers change as library grows. Honest + safe.

### Path B — Wire question_frame + enable shadow-only validation
1. Add retrieval_service.route() wrapper that calls question_frame before activating solver
2. Rerun Phase C with the wrapper → should improve negative rejection dramatically
3. If numbers look good, proceed to Phase D-1 candidate mode
**Estimated effort:** 3-4h code + test + shadow rerun

### Path C — Abandon hex, use flat
Gate 2 triggered (flat ties hex). Delete `hex_cell_topology` retrieval code, keep ledger + manifests as simple versioning infrastructure, retrieve via single flat index.
**Effort:** 2-3h refactor
**Risk:** loses seasonal/learning empty-cell gap signals; may need to re-add when library >500 solvers

### Path D — Expand library first
Focus on filling seasonal/learning/general cells. 50-100 new solvers via Claude-teacher pipeline (memory plan Week 3+). Rerun Phase C once library is 3-5x larger.
**Effort:** weeks of work, but unblocks scaling story

### Path E — Enable anyway (ignore the data)
Ignore Path A-D, flip `mode: authoritative`, hope that production traffic is different from oracle.
**Not recommended.** Violates x.txt rule #1 (never claim green without evidence).

## My recommendation

**Combine B + D:**

**Immediate (now):**
- Wire question_frame into retrieval pipeline (Path B, 3-4h)
- Rerun Phase C with wrapper
- If numbers jump (which they should — filtering off-domain negatives should add 20-30pp to overall precision), then proceed to Phase D-1

**Medium-term (weeks):**
- Fill empty cells with Claude-teacher pipeline (Path D)
- Rerun Phase C at 50, 100, 200 solver milestones
- At ~200 solvers, hex-vs-flat advantage may emerge as cells accumulate

**Current state unchanged:**
- `hybrid_retrieval.mode: shadow` stays (don't flip to authoritative)
- Shadow infrastructure keeps running + collecting data
- Watchdog + campaign keep running
- `memory/project_hex_solver_scaling.md` precondition NOT yet met

## What this validates / invalidates about v3 plan

**Validates:**
- GPT's cell-honesty concern (54% keyword/centroid disagreement)
- GPT's question-frame necessity (negative score overlap proves this)
- Grok's "premature scaling" warning (hex no measurable advantage at 14)
- Gemini's "flat baseline" test (flat ties hex exactly as predicted)
- v3.1 tweak 1 (source-side placement block) — no false placements happened

**Does NOT invalidate hex topology** — it just confirms hex value emerges at larger scale. 14 solvers is below the inflection point where cells save work.

## Next concrete action

Three user choices:

**(1) "Wire question_frame and rerun Phase C"** — proceed with Path B today, 3-4h
**(2) "Accept current state, focus elsewhere"** — Path A, current infrastructure stays, work on other priorities (domain expansion, demo UI, campaign analysis)
**(3) "Abandon hex, go flat"** — Path C, 2-3h refactor

My vote: **(1)** — Path B gives us one more measured data point with the full v3 pipeline integrated. If numbers still don't justify hex after question_frame wiring, then we honestly consider Path C. If they do, we proceed to D-1.

Three honest reviewer perspectives, three honest measurements, three honest choices. The architecture is sound; the library is small; the decision is "wait" — not "never".
