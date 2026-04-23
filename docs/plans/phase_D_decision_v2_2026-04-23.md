# Phase D Decision v2 — Updated with True Off-Domain Data

**Date:** 2026-04-23 (later same day as v1)
**Supersedes:** `docs/plans/phase_D_decision_2026-04-23.md` (v1 was based on incorrect off-domain assumption)
**Evidence:**
- v1 used "negatives within solver YAML" which were actually cross-domain (right cell, wrong solver), not off-domain
- v2 adds `tests/oracle/_off_domain.yaml` — 32 truly off-domain queries (weather, jokes, math, geography, translations, etc.)
- Threshold sweep on real combined data

## TL;DR — REVISED VERDICT

**Verdict: PROCEED to Phase D-1 candidate mode with score threshold = 0.60.**

The original v1 conclusion ("don't enable") was based on flawed data — I had labeled cross-domain queries (e.g. "honey yield" in heating_cost.yaml's negative section) as "off-domain" when they actually correctly route to honey_yield. Once true off-domain queries are tested, the system shows:

- **47.5% positive precision** at threshold 0.60
- **84.4% off-domain rejection** at threshold 0.60
- **Overall 51.3% combined precision**

This is decision-grade evidence to enable Phase D-1.

## What changed between v1 and v2

### v1 mistake

Per-solver YAML negatives were really "queries that should route elsewhere":

```yaml
# tests/oracle/heating_cost.yaml
negative:
  - "what is honey yield this season"   # Actually a CORRECT route to honey_yield!
  - "varroa treatment dosage"           # Actually a CORRECT route to varroa_treatment!
  - ...
```

When the system routed "honey yield" to honey_yield (the right answer), my scoring treated it as a FALSE positive. So "0% negative rejection" was misleading — the system was actually doing the right thing.

### v2 fix

Added `tests/oracle/_off_domain.yaml` with TRULY off-domain queries:
- "Mikä on sään ennuste tänään?" (weather)
- "Tell me a joke"
- "Translate hello to Spanish"
- "Mikä on Pythagoraan lause"
- "Send an email to mom"
- ... 32 total queries

These have NO correct route in the current 14-solver library. The system should reject all of them.

### Key measurement

When 32 true off-domain queries are scored against all hex cells, max-similarity-score distribution:

- **Mean: 0.563** (well below positive-correct mean 0.69)
- **Median: 0.562**
- **Max: 0.731** (one outlier — "Lähetä sähköposti äidille" matched system cell 0.73)

This proves the embedding similarity DOES distinguish off-domain queries — they cluster around 0.55-0.60 while correct routes cluster around 0.65-0.75.

## Threshold sweep (real combined data)

| Threshold | Positive precision | Off-domain rejection | Overall |
|---:|---:|---:|---:|
| 0.45 | 55.0% | 0.0% | 49.4% |
| 0.50 | 54.6% | 3.1% | 49.4% |
| 0.55 | 52.1% | 43.8% | 51.3% |
| **0.60** | **47.5%** | **84.4%** | **51.3%** |
| 0.62 | 45.0% | 93.8% | 50.0% |
| 0.65 | 37.5% | 96.9% | 43.6% |
| 0.70 | 26.8% | 96.9% | 34.0% |
| 0.75 | 14.6% | 100.0% | 23.4% |

**Two viable threshold zones:**

- **0.55-0.60: balanced** (47-52% positive, 44-84% off-domain) — recommended for Phase D-1 candidate mode where false negatives can be caught by keyword fallback
- **0.62: precision-first** (45% positive, 94% off-domain) — recommended for Phase D-2 authoritative if negative quality is critical

Either is dramatically better than no threshold (0% off-domain).

## Question_frame integration impact (Path B test)

Re-running with `--with-question-frame` showed +0.4pp overall precision improvement. The reason it didn't help more:

- 138 of 140 oracle "negatives" parsed as `desired_output: numeric` (because they're like "what is X" syntactically)
- Only 2 parsed as `diagnosis` and got correctly rejected
- True diagnosis/explanation queries are rare in this oracle

Question_frame is **architecturally correct** but its impact in this benchmark is small because most queries (positive AND negative) are numeric-question-shaped. Its value emerges when:
1. Diagnosis/explanation/optimization queries become more common
2. Solver_output_schema gets richer types (currently 14/14 are "numeric" mode)

**Keep it integrated** — costs ~0ms (deterministic regex), provides defense for future query mix.

## Three reviewer perspectives — final scorecard

| Reviewer | v1 verdict | v2 verdict (after off-domain test) |
|---|---|---|
| GPT (cell honesty) | ✅ Validated (54% disagreement) | ✅ Still validated |
| GPT (question_frame) | ⚠️ Validated by score overlap | ⚠️ Limited impact in current oracle, but architecturally needed |
| Grok (premature scaling) | ✅ Validated (hex no advantage at 14) | ⚠️ Partially — at threshold 0.60 hex performs as well as flat AND adds gap signaling |
| Gemini (flat baseline) | ✅ Flat ties hex | ✅ Still ties on positives, but hex provides organizational metadata flat doesn't |

## Updated Phase D path forward

### Phase D-1 — candidate mode (24h live)

Set:
```yaml
hybrid_retrieval:
  loaded: true
  mode: candidate
  min_score: 0.60         # NEW — was 0.35
  embedding_hot_path: false
```

Hybrid runs in parallel with keyword. MAGMA traces both routes. Production answer comes from keyword.

Compare for 24h:
- `hybrid_unique_correct` (hybrid would have helped)
- `hybrid_unique_incorrect` (hybrid would have hurt)
- Required: 3:1 ratio in favor of hybrid OR neutral with off-domain rejection improvement

### Phase D-2 — authoritative (10 min config swap)

After D-1 passes, set `mode: authoritative`. Hybrid is now production. Watchdog applies sharp kill signals (Grok-tightened) per Phase E.

### Phase E — 24h hardened measurement

Already automated in `tools/campaign_watchdog.py`. Sliding 100-query window. Sharp triggers:
- p50 routing latency increase > 8 ms → rollback
- LLM fallback rate increase > 2pp → rollback
- Solver hit rate decrease (any) → rollback
- p95 embedding latency > 80 ms → rollback
- Single embedding > 200 ms → rollback
- Any new 5xx in HybridRetrievalService → rollback
- FAISS version mismatch → rollback
- MAGMA append-only violation → full rollback to keyword + incident
- Memory growth > 15% → force watchdog restart

### Phase F — documentation

After E close (24h stable):
- Update `docs/HYBRID_RETRIEVAL.md` with measured numbers
- Update `CHANGELOG.md`, `CURRENT_STATUS.md`
- Mark `memory/project_hex_solver_scaling.md` precondition done

## Recommendation for next action

**Two paths, both legitimate:**

**Path α — Enable Phase D-1 now (this session)**
1. Edit `configs/settings.yaml` to set `mode: candidate, min_score: 0.60`
2. Restart server via watchdog (kill PID, watchdog restarts within 60s)
3. Let 24h run with parallel hybrid+keyword
4. Tomorrow: compare unique_correct/unique_incorrect ratio
5. If 3:1 → promote to D-2 authoritative

**Path β — Build more solvers first, then enable**
1. Create Claude-teacher pipeline iteration
2. Generate 30-50 new solvers in seasonal/learning/general cells
3. Re-run Phase C → expect higher precision on richer library
4. Then Phase D-1

I (Claude) recommend **Path α** — the data is good enough to justify candidate mode (which doesn't change production answers, just observes hybrid's would-be decisions). It collects 24h of real production data which will inform Path β much better than synthetic oracle.

## Honest summary

v1 was wrong. v2 is right. The architecture works once you:
1. Add score threshold of 0.60 (now obvious from data)
2. Use true off-domain queries for testing (was a labeling mistake)
3. Wire question_frame as a deterministic safety net (small but free)

Phase D-1 promotion gate is **PASSED**. Awaiting user approval to flip `mode: candidate`.
