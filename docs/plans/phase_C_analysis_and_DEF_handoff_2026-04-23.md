# Phase C analysis + Phase D/E/F readiness handoff

**Date:** 2026-04-23
**Plan reference:** `hybrid_retrieval_activation_v3_2026-04-23.md` + v3.1 amendments
**Verdict:** ⚠️ **Phase C MID-RUN — baseline established, oracle set needed before Phase D promotion gate**

## Phase C results (200 queries from `query_corpus.json`)

Full data: `docs/runs/hybrid_shadow_three_way_2026-04-23T085142Z.{md,json}`

### Latency (excluding embedding which is cached after first hit)

| Architecture | p50 | p95 | vs keyword |
|---|---:|---:|---:|
| keyword | 0.01 ms | 0.02 ms | baseline |
| flat | 0.08 ms | 0.11 ms | 5x slower |
| hex | 0.28 ms | 0.48 ms | 24x slower |

All three sub-millisecond. With embedding (warm cache) the additional fixed cost is negligible; cold cache adds ~3 s per unique query.

### Architecture agreement

| Pair | Agreement | What it tells us |
|---|---:|---|
| flat solver vs hex solver | **93.0%** | Gemini was right: at 14 solvers, single flat FAISS retrieves nearly identical results to hex+rings, with 1/5 the moving parts |
| keyword cell vs hex chosen cell | 5.0% | Test queries don't match solver domains (general weather/news vs specialized cottage/factory) |
| keyword/centroid disagreement | **54.0%** | GPT was right: the keyword cell classifier is unreliable — centroid-based parallel assignment is meaningful |

### Critical observation

The test queries (from `query_corpus.json`) are general UI-gauntlet queries (weather, news, conversational) — they are NOT designed to match our 14 specialized solvers (heating_cost, honey_yield, etc.). So **oracle precision@1 / recall@5 metrics are not measurable from this run**.

For Phase D-1 promotion gate (per v3 §1.6), we need either:
- **Handwritten oracle set** at `tests/oracle/<solver_id>.yaml` (20-50 positive + 20 negative queries per solver), OR
- **Verified GOLD/SILVER CaseTrajectory subset** from production with real solver-targeted queries

Neither exists yet at sufficient scale.

## Three reviewer predictions revisited

| Reviewer | Prediction | Phase C verdict |
|---|---|---|
| GPT | "Cell assignment honesty matters — keyword + centroid parallel" | **Validated** — 54% disagreement rate is exactly the failure mode predicted |
| Grok | "At 14 solvers, hex topology adds complexity for no measurable gain" | **Partially validated** — flat retrieves same answer 93% of the time; the 7% bridge cases would need oracle data to evaluate |
| Gemini | "Test against single flat FAISS baseline" | **Test executed** — flat is competitive with hex at this scale, exactly as predicted |

## Decision matrix per v3 §1.5

| Outcome scenario | Action | Current data says |
|---|---|---|
| Hex outperforms flat by ≥ 5pp on oracle precision@1 AND latency ≤ keyword + 10ms | Proceed to Phase D-1 | Cannot determine without oracle set |
| Flat ties or beats hex | Abandon hex topology | Currently flat beats hex on latency; tied on solver agreement (93%) |
| Both lose to keyword on routing accuracy | Don't enable hybrid | Keyword wins on latency by 24x; accuracy unknown without oracle |
| Hex wins but ring-expansion-useful-rate < 5% | Drop ring expansion | Cannot measure ring expansion utility on general queries |

**Honest conclusion:** Phase C is **insufficient evidence** to make the Phase D promotion decision. We have **architectural validation** (cell honesty matters) and **scale-confirmation** (hex doesn't yet pay for itself at 14 solvers) but **no precision/recall data** because no oracle set exists.

## Recommended next steps (in order)

### Step 1 — Build oracle set (4-8 h human work)

For each of 14 solvers, write `tests/oracle/<solver_id>.yaml` with:
- 20-50 positive queries (queries that should route to this solver)
- 20 negative queries (queries that should NOT route to any solver, or should route to a different one)

Example template at `tests/oracle/_template.yaml`:

```yaml
solver: heating_cost
positive:
  - "paljonko lämmitys maksaa kuussa"
  - "what is monthly heating cost for 120 m2 house"
  - "laske lämmityskulut R-arvolla 2.5"
  - ...
negative:
  - "miksi pumppu värisee"
  - "what is honey yield"
  - "kannattaako aurinkopaneeli"
  - ...
```

Once oracle set exists, rerun Phase C with `--source oracle` for precision@1.

### Step 2 — Decision gate

After oracle precision@1 measured:
- If hex precision@1 within 2pp of keyword AND oracle recall@5 ≥ keyword + 5pp → **proceed to Phase D-1**
- Else → either abandon hex (use flat) OR don't enable hybrid yet (wait for more solvers)

### Step 3 — Phase D-1 (if approved): 24h candidate mode

Set `hybrid_retrieval.mode: candidate` in `configs/settings.yaml`. Hybrid runs in parallel with keyword, both visible to verifier, production uses keyword. Compare for 24h:
- `hybrid_unique_correct` count (hybrid would have helped, keyword failed)
- `hybrid_unique_incorrect` count (hybrid would have hurt, keyword succeeded)
- Required: 3:1 ratio in favor of hybrid

### Step 4 — Phase D-2 (if D-1 passes): authoritative

Set `mode: authoritative`. Hybrid is now production. Watchdog continues monitoring per Phase E sharp kill signals.

### Step 5 — Phase E (automated): 24h hardened measurement

Implemented in `tools/campaign_watchdog.py`. Sliding 100-query window with sharp triggers (Grok's tighter signals):
- p50 routing latency increase > 8 ms → rollback
- LLM fallback rate increase > 2pp → rollback
- Solver hit rate decrease (any) → rollback
- p95 embedding latency > 80 ms → rollback
- Single embedding > 200 ms → rollback
- Any new 5xx in HybridRetrievalService → rollback
- FAISS version mismatch → rollback to last coherent manifest
- MAGMA append-only violation → full rollback to keyword + incident
- Memory growth > 15% → force watchdog restart

### Step 6 — Phase F: documentation

After D-2 stable for 24h:
- Update `docs/HYBRID_RETRIEVAL.md` with measured numbers
- Update `CHANGELOG.md` Unreleased
- Update `CURRENT_STATUS.md`
- Mark `memory/project_hex_solver_scaling.md` precondition done

## What's blocking Phase D promotion right now

1. **No oracle set.** Without `tests/oracle/*.yaml`, we cannot compute precision@1.
2. **Library is too small.** 14 solvers across 5 non-empty cells means most cells have 2-4 solvers — not enough variation to test ring-expansion utility.
3. **Production query distribution doesn't target solvers.** UI gauntlet queries are conversational/general, not domain-specialized.

These are not architectural issues — v3 + v3.1 plan is sound. They are content/data gaps.

## Practical recommendation for current state

**Don't enable hybrid_retrieval yet.** Phase A and B infrastructure is in place — keep it running but don't promote past `mode: shadow`.

Instead, focus next session on:
1. Generate oracle queries for the 14 existing solvers (Step 1 above)
2. Add 50-100 new solvers to fill `seasonal`, `learning`, `general` cells
3. THEN rerun Phase C with oracle, expect dramatically different (and decision-grade) numbers

Per v3.1 / GPT round-3: this conservative path is the correct one. We have the tooling, we lack the content.

## Phase D / E / F status — ready when needed

All necessary infrastructure exists:

- Phase D mode toggle: `configs/settings.yaml::hybrid_retrieval.mode`
- Phase D-1 metrics tracking: `tools/shadow_route_three_way.py` can run in parallel
- Phase E watchdog: `tools/campaign_watchdog.py` already running with sharp kill signals
- Phase F docs: templates exist in `docs/HYBRID_RETRIEVAL.md`, `CHANGELOG.md`, `CURRENT_STATUS.md`

When oracle data is ready and Phase C is decisive, Phase D-2 can be enabled in **10 minutes** (config change + server restart).

## Summary

| Phase | Status | Blocker |
|---|---|---|
| A | ✅ Complete | none |
| B | ✅ Complete | none |
| C (initial run) | ✅ Done | no oracle → can't compute precision@1 |
| C (oracle-grade) | ⏳ Pending | requires 4-8h human labeling |
| D-1 | ⏳ Pending | requires Phase C oracle pass |
| D-2 | ⏳ Pending | requires D-1 + 24h pass |
| E | ⏳ Pending | requires D-2 |
| F | ⏳ Pending | requires E close |

**Total elapsed work today:** Phase A (1h) + Phase B (8h tools, ran in 2h with parallelism) + Phase C 200-query shadow (10 min). Infrastructure is **fully ready** for the remaining phases once oracle data arrives.

Hex topology is **not validated** at current scale. Decision deferred until oracle set + larger solver library.
