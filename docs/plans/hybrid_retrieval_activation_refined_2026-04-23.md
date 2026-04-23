# Refined Plan: Activate `hybrid_retrieval` in WaggleDance — Synthesized from GPT-5 + Grok 4 + Gemini 2.5 Pro Reviews

**Author:** Jani Korpi + Claude Sonnet 4.6 (synthesis)
**Date:** 2026-04-23
**Supersedes:** `docs/plans/hybrid_retrieval_activation_review_2026-04-23.md`
**Reviews integrated:**
- `docs/plans/GPT.txt` (GPT-5, 30-min architecture review)
- `docs/plans/grock.txt` (Grok 4, premise-challenge review)
- `docs/plans/gemini.txt` (Gemini 2.5 Pro, library-implementation review)
- `docs/plans/hex_topology_performance_metrics_2026-04-23.md` (GPT-5 metrics spec)

**Status:** Implementation-ready, but DO NOT execute Phase D (production enable) without explicit approval from Jani after Phase C oracle results land.

---

## 0. Why this version differs from the original plan

The original plan proposed: build axiom backfill → shadow validation → flip `enabled: true`. Three reviewers found independent critical gaps:

| Reviewer | Critical gap they found |
|---|---|
| GPT-5 | Cell assignment is still keyword-based, so embedding can't recover from wrong cell choice — this LOCKS the win to keyword's quality. |
| Grok 4 | No-GPU edge deployment (Raspberry Pi) makes embedding 80-180 ms — breaks the "<1 ms solver-first" promise. The original plan didn't budget for the no-GPU hot path. |
| Gemini 2.5 Pro | The whole hex+ring+Chroma stack hasn't been measured against a single flat FAISS index — Phase C should A/B those two architectures, not hybrid vs keyword. |

All three converge on: **don't flip `enabled: true` immediately**. Use a three-stage activation (shadow → candidate → authoritative) with sharp rollback signals.

This refined plan integrates every contribution and adds two pieces none of the reviewers flagged:
- **Per-cell version manifest** with atomic rollback (versioning hex at 8+ cells is non-trivial)
- **Embedding model migration tool** (when nomic-embed v3 ships, the entire corpus must re-embed — needs a documented procedure)

---

## 1. Architectural decisions (with reviewer attribution)

### 1.1 Activation modes (GPT-5)

```yaml
hybrid_retrieval:
  enabled: true             # whether the hybrid layer is loaded at all
  mode: shadow              # shadow | candidate | authoritative
  ring2_enabled: false
  min_score: 0.35
  sufficient_score: 0.7
  embedding_hot_path: false # GROK addition — see §1.4
```

| Mode | Behavior | When to use |
|---|---|---|
| `shadow` | Hybrid computes candidates, MAGMA traces both, production uses old route | Phase C |
| `candidate` | Hybrid candidate visible to verifier, can be overridden by old route | Phase D-1 |
| `authoritative` | Hybrid chooses production solver path | Phase D-2 (only after gates pass) |

### 1.2 Cell assignment honesty (GPT-5)

**Problem:** Current `hex_cell_topology.assign_cell()` is keyword-based. Embedding only ranks within the (possibly wrong) cell. Hybrid wins disappear when the coarse cell choice is wrong.

**Solution:** Compute BOTH for every query:
- `keyword_cell` — from existing `assign_cell(intent, query)`
- `centroid_cell_top3` — from cosine similarity vs each non-empty cell's centroid

**Candidate cells searched** = union of:
- `keyword_cell`
- `centroid_cell_top3`
- non-empty ring1 of `keyword_cell`
- non-empty ring1 of `centroid_top1`

Log `keyword_centroid_disagreement = (keyword_cell != centroid_top1)` to MAGMA trace. Track rate per domain.

### 1.3 Empty cell policy (GPT-5)

`seasonal` and `learning` cells are currently empty. Treatment:

```python
if target_cell.is_empty:
    record_empty_cell_miss(cell_id, query_hash, intent, centroid_score)
    search_non_empty_ring1_neighbors()
    if no_candidate_above_threshold:
        search_global_chroma()
    if still_no_candidate:
        fallback_to_llm()
# NO auto-merge into largest neighbor — empty cells are honest gap signals
```

Cell status published in `/api/status`:
```yaml
cell_status:
  seasonal:
    solver_count: 0
    routing_allowed: true     # query can semantically belong here
    search_allowed: false     # but we don't search this cell's empty index
    fallback_policy: ring1_then_global_then_llm
```

### 1.4 No-GPU lazy embedding hot path (GROK 4 — most critical addition)

WaggleDance ships `--preset=raspberry-pi-iot`. Nomic-embed-text on RPi 5 CPU: **80-180 ms per embedding**. This breaks the "solver-first <1 ms" promise on edge.

**Decision:** Embedding is NOT the hot path. Keyword routing remains the hot path. Embedding runs only when:

```python
def route(query):
    keyword_route = router_v2.route(query)  # ~0.1 ms always
    if keyword_route.confidence >= sufficient_score:  # 0.7
        return keyword_route  # fast path — embedding never called
    # Slow path: embed and consult hex retrieval
    return hybrid_retrieval.retrieve(query, fallback_keyword=keyword_route)
```

**Result:** ~80% of queries (typical confident keyword matches) skip embedding entirely. Only ambiguous/novel queries pay the embedding cost. This preserves the edge-deployment story.

This setting is `embedding_hot_path: false`. Setting it to `true` reverses the polarity (embed always) for high-end deployments where the GPU+latency budget allows it.

### 1.5 Hex vs flat A/B test (Gemini 2.5 Pro)

Phase C runs THREE configurations side-by-side on the same query set:

| Config | Architecture | Purpose |
|---|---|---|
| `keyword` | Current production | Baseline |
| `flat` | Single global FAISS IndexFlatIP, no cells | Gemini's challenge |
| `hex` | Cells + ring1 + ring2 + global | The hypothesis |

If `hex` outperforms `flat` by < 5pp on oracle precision@1 AND adds > 10 ms p50 latency, **abandon hex topology** and go with flat. This is the explicit measurement Grok asked for and Gemini designed.

### 1.6 Three independent ground truth sources (GPT-5)

Replace "keyword agreement = correctness" with:

**A. Axiom oracle set** — for each solver, hand-write 20-50 positive queries and 20 negative queries:
```yaml
solver: heating_cost
positive:
  - "paljonko lämmitys maksaa"
  - "what is heating cost for 120 kWh at 0.18 €/kWh"
  - "laske mökin lämmityskulut"
negative:
  - "onko lämpöpumppu rikki"
  - "milloin kannattaa sulkea ilmanvaihto"
  - "mikä on hunajan tuotto"
```

Stored at `tests/oracle/<solver_id>.yaml`. Generated initial set semi-automatically from solver descriptions; validated by hand for top 20 solvers.

**B. Verified GOLD/SILVER CaseTrajectory subset.** Filter campaign data to:
```python
trajectories = [t for t in case_store
                 if t.route_type == "solver"
                 and t.verifier_pass == True
                 and t.quality_tier in ("GOLD", "SILVER")]
```
This is post-execution evidence that the keyword router DID pick a solver and the answer DID verify. Use as an additional truth signal.

**C. Blind adjudication set.** 200-500 queries from `hot_results.jsonl`, presented to a separate AI (or human) without showing the keyword router's decision. Label: `expected_solver_id` or `expected_no_solver`. Cost: ~2 hours of one annotator. Output: `tests/oracle/blind_adjudication.yaml`.

### 1.7 Multi-view embeddings (GPT-5)

Per solver, index 4-5 views with same `canonical_solver_id`:

```yaml
canonical_solver_id: heating_cost
views:
  - view_type: canonical_en
    text: "Cottage heating cost estimate. Calculate electricity cost..."
  - view_type: canonical_fi
    text: "Mökin lämmityskustannus. Paljonko lämmitys maksaa..."
  - view_type: synonym_mixed
    text: "heating lämmitys cost kustannus electricity sähkö kWh euro..."
  - view_type: unit_variable
    text: "kwh_price daily_kwh days area_m2 R_value temperature_diff"
  - view_type: example_queries
    text: "paljonko lämmitys maksaa; what does heating cost; sähkön hinta..."
```

Top-k search must dedupe by `canonical_solver_id` so one solver doesn't fill all 5 top-5 slots with its own aliases.

### 1.8 Negation/question-type parser (GPT-5)

Embedding finds *which solver* but not *what answer shape*. Add a small deterministic parser between retrieval and solver execution:

```yaml
question_frame:
  desired_output: numeric | boolean_comparison | explanation | diagnosis | optimization
  comparator:
    op: ">" | "<" | ">=" | "<=" | "=" | null
    threshold: number | null
    unit: string | null
  negation:
    present: bool
    scope: string | null
```

Example: `"onko lämmityskustannus yli 50 €?"` →
```python
solver = "heating_cost"        # from retrieval
postprocessor = "compare(output.cost_eur > 50)"   # from question_frame
answer_type = "boolean_with_explanation"
```

Implementation: regex + small word-list parser. Tests required for FI/EN negation, threshold detection, unit conversion.

### 1.9 FAISS index safety (GPT-5 + Gemini 2.5 Pro)

**RCU/copy-on-write snapshot pattern:**

```text
Backfill writes to data/faiss_staging/cell_<id>/
Validation reads staging snapshot
On approval: os.replace(data/faiss_staging/, data/faiss_live/)  # atomic
Live readers use immutable snapshot pointer
Dream Mode writes to staging only, never live
Every retrieval trace records faiss_index_version
```

Each cell has `version_manifest.json`:
```yaml
cell_id: thermal
faiss_index_version: 2026-04-23T15:30:00Z-3a4f
embedding_model: nomic-embed-text:v1.5
embedding_dim: 768
solver_count: 4
canonical_solver_ids: [heating_cost, hive_thermal_balance, pipe_freezing, heat_pump_cop]
checksum_sha256: <hash>
created_at: 2026-04-23T15:30:00Z
created_by: tools/backfill_axioms_to_hex.py @ commit 03b4678
```

`os.replace(tmp, final)` is the only Windows-atomic primitive (Gemini). Backfill writes `cell_thermal.index.tmp`, then `os.replace`.

### 1.10 Per-cell version manifest + rollback (Claude addition)

Hex with 8+ cells means 8+ FAISS indices that can drift. Need atomic-rollback-to-coherent-state:

```yaml
# data/faiss_live/manifest.json
manifest_version: 2026-04-23T16:00:00Z-coherent-set-A
cells:
  thermal:    {version: 2026-04-23T15:30:00Z-3a4f, status: live}
  energy:     {version: 2026-04-23T15:30:01Z-7b2e, status: live}
  safety:     {version: 2026-04-23T15:30:02Z-9c1d, status: live}
  general:    {version: 2026-04-23T15:30:03Z-2f8a, status: live}
  math:       {version: 2026-04-23T15:30:04Z-4d6b, status: live}
  system:     {version: 2026-04-23T15:30:05Z-8e3c, status: live}
  seasonal:   {version: empty, status: empty}
  learning:   {version: empty, status: empty}
```

`tools/hex_rollback.py --to-manifest <version>` swaps all 8 cells back to a known-coherent set atomically. Useful when one cell's update introduces regression.

### 1.11 Embedding model migration tool (Claude addition)

When `nomic-embed-text` upgrades v1.5 → v2 (or we switch to BGE), the entire corpus must re-embed. Document the procedure now:

```text
tools/migrate_embedding_model.py:
  1. Read current manifest_version
  2. For each cell:
     - Read all docs (text + canonical_solver_id) from current FAISS index
     - Re-embed with new model
     - Write to staging with new model_version tag
  3. Run Phase C oracle validation against staging
  4. If pass: atomic swap manifest_version → new
  5. If fail: discard staging, report

Embedding model is recorded in trace, so old traces remain interpretable.
```

---

## 2. Phases (revised)

### Phase A — Pre-flight (30 min)

| Step | Action | Pass criterion |
|---|---|---|
| A.1 | `pip list \| grep faiss-cpu` | `faiss-cpu==1.13.2` installed |
| A.2 | Test Ollama new endpoint: `curl -X POST http://ollama:11434/api/embed -d '{"model":"nomic-embed-text","input":["test1","test2"]}'` (Gemini: use `/api/embed` not `/api/embeddings`) | 200 OK with array of vectors |
| A.3 | Measure embedding latency on this hardware: 100 calls, batch=1, batch=16, batch=32. Record p50/p95. | Document baseline numbers |
| A.4 | If embedding p95 > 50 ms: `embedding_hot_path: false` is mandatory. If p95 < 10 ms: optional. | Decision recorded |
| A.5 | Backup `configs/settings.yaml` and `data/faiss/` to `backup/2026-04-23/` | Files copied |

### Phase B — Tools (5h)

#### B.0 — Axiom backfill with multi-view embeddings

`tools/backfill_axioms_to_hex.py`:

```python
for axiom_path in glob("configs/axioms/*/*.yaml"):
    axiom = yaml.safe_load(open(axiom_path))
    cid = solver_hash.canonical_hash(axiom)

    # Skip if already in registry with same hash (idempotency)
    if registry.has_canonical_solver(axiom["model_id"], cid):
        continue

    views = build_multi_view(axiom)  # canonical_en, canonical_fi, synonym_mixed, unit_variable, example_queries

    cell = topology.assign_cell(intent="symbolic", query=axiom["model_name"]).cell_id

    # Batch-embed the views (Gemini: batch 16-32)
    vectors = ollama_embed_batch([v["text"] for v in views], batch_size=16)

    # Write to STAGING (never live)
    staging_index = registry.get_or_create_staging(f"cell_{cell}")
    for view, vec in zip(views, vectors):
        staging_index.add(
            doc_id=f"{axiom['model_id']}#{view['view_type']}",
            text=view["text"],
            vector=vec,
            metadata={
                "canonical_solver_id": axiom["model_id"],
                "view_type": view["view_type"],
                "view_lang": view.get("lang"),
                "source": "axiom",
                "axiom_file": str(axiom_path),
                "canonical_hash": cid,
                "embedding_model": "nomic-embed-text:v1.5",
            },
        )

# After all writes: persist staging atomically with version tag
registry.persist_staging(version=f"{datetime.utcnow().isoformat()}Z-{git_sha[:4]}")
```

Tests (10):
1. Idempotency — rerun on same axioms produces identical staging
2. Cell assignment determinism
3. Embedding error handling (Ollama down → graceful fail, no partial write)
4. `--filter-domain` works
5. `--dry-run` doesn't write
6. Hash dedup against `solver_hash.canonical_hash`
7. Multi-view: 5 docs per axiom indexed
8. Top-k dedup by canonical_solver_id (post-search)
9. Staging never overwrites live
10. `os.replace(staging, live)` atomic on Windows (mocked)

#### B.1 — Cell centroid computation

`tools/compute_cell_centroids.py`:

```python
for cell in non_empty_cells:
    vectors = registry.get_staging(f"cell_{cell}").all_vectors()
    centroid = vectors.mean(axis=0)
    centroids[cell] = centroid

write("data/faiss_staging/cell_centroids.json", centroids)
```

Used by `assign_cell_embedding(query_vec)` for the centroid-based parallel cell choice (§1.2).

#### B.2 — Three-architecture shadow runner

`tools/shadow_route_three_way.py`:

```python
queries = sample_recent(hot_results.jsonl, n=1000)
for q in queries:
    # Architecture A: keyword (current production)
    a = router_v2.route(q.text)

    # Architecture B: flat (Gemini's challenge)
    a_flat = flat_index.search(embed(q.text), k=5)

    # Architecture C: hex (this proposal)
    a_hex = hybrid_retrieval.retrieve(q.text, mode="shadow")

    record_three_way(query=q, keyword=a, flat=a_flat, hex=a_hex)
```

Output: `docs/runs/hybrid_shadow_three_way_<date>.md` with per-architecture metrics.

#### B.3 — Per-cell version manifest tool

`tools/hex_manifest.py {commit | rollback | status}`:
- `commit` — promotes staging → live atomically with new manifest version
- `rollback --to-version <id>` — atomic swap back to a previous coherent set
- `status` — shows which version each cell is on, drift warnings

#### B.4 — Embedding model migration scaffold

`tools/migrate_embedding_model.py` — placeholder for future use, documents the procedure (see §1.11). Not run now, but exists in repo so the procedure isn't lost.

#### B.5 — Question-frame parser

`waggledance/core/reasoning/question_frame.py`:
- Regex + word list for negation detection (FI: `ei`, `älä`; EN: `no`, `not`, `don't`)
- Threshold detection: `> 50€`, `yli 50 €`, `over 50 EUR`
- Unit normalization: `€` ≡ `EUR`, `kWh` ≡ `kilowatt-hour`
- Output type: numeric / boolean / explanation / diagnosis / optimization

Tests: 30 cases covering FI/EN positive, FI/EN negative, threshold parsing, unit conversion, ambiguous cases.

### Phase C — Oracle-first shadow validation (8h, can run overnight)

Three truth sources (§1.6) feed into the metrics. Three architectures (§1.5) compete:

| Metric | Keyword baseline | Flat | Hex (ours) | Threshold for hex to win |
|---|---|---|---|---|
| Oracle precision@1 | TBD | TBD | TBD | hex ≥ keyword - 2pp AND hex ≥ flat - 1pp |
| Oracle recall@5 | TBD | TBD | TBD | hex ≥ keyword + 5pp AND hex ≥ flat + 2pp |
| False solver activation | TBD | TBD | TBD | ≤ 1% across all three |
| False LLM fallback | TBD | TBD | TBD | hex < keyword by 5pp |
| Median routing latency | ~0.1 ms | TBD | TBD | hex ≤ keyword + 5 ms (with `embedding_hot_path: false`) |
| p95 routing latency | TBD | TBD | TBD | hex ≤ keyword + 25 ms |
| Ring expansion useful rate (Grok) | n/a | n/a | TBD | ≥ 5% (else hex topology has no value, switch to flat) |
| FI recall@5 vs EN recall@5 (GPT) | TBD | TBD | TBD | within 5pp |
| Mixed-language recall@5 | TBD | TBD | TBD | ≥ 80% |

Output: `docs/runs/hybrid_shadow_three_way_2026-04-XX.md` + machine-readable `data/shadow_metrics.json`.

**Decision gates after Phase C:**

| Outcome | Action |
|---|---|
| Hex wins on all metrics | Proceed to Phase D-1 (candidate mode) |
| Flat ties or beats hex | **Abandon hex topology**, refactor to flat-only architecture, restart Phase B with simplified design |
| Both lose to keyword | **Don't enable** — keyword routing is sufficient at this scale (this is Grok's "premature scaling" verdict) |
| Hex wins but Grok's ring-expansion-useful-rate < 5% | Keep hex shape, drop ring1/ring2 search expansion (savings); revisit at 200+ solvers |

### Phase D-1 — Candidate mode (24h)

Set `mode: candidate`. Hybrid computes candidate, MAGMA traces both routes, but production answer comes from old route. Compare:

- For 24 hours, every query gets BOTH a keyword answer and a hybrid candidate
- Verifier scores both
- If hybrid candidate would have produced a verifier-pass and keyword failed → log `hybrid_unique_correct`
- If hybrid would have failed and keyword succeeded → log `hybrid_unique_incorrect`

**Pass criterion to advance to D-2:**
- `hybrid_unique_correct / hybrid_unique_incorrect` ≥ 3:1
- Zero new XSS/DOM/auth regressions
- p95 routing latency stable (hybrid runs in parallel, must not block)

### Phase D-2 — Authoritative mode (production)

Flip `mode: authoritative`. Hybrid is now the production router. Watchdog continues monitoring.

### Phase E — 24h hardened measurement (Grok's sharp kill signals)

Sliding 100-query window. Rollback IMMEDIATELY (not "consider rollback") if ANY trigger:

| Kill signal (Grok-tightened) | Threshold | Action |
|---|---|---|
| Median routing latency increase | > 8 ms | rollback to candidate |
| LLM fallback rate increase | > 2 pp | rollback to candidate |
| Solver hit rate decrease | any | rollback to candidate |
| p95 embedding latency | > 80 ms | rollback to candidate |
| Single embedding latency | > 200 ms | rollback to candidate |
| New 5xx in HybridRetrievalService | any | rollback to candidate |
| FAISS version mismatch (different cells on different versions) | any | rollback to last coherent manifest |
| Untraced solver activation | any | rollback to candidate |
| MAGMA append-only violation | any | full rollback to keyword + incident |
| Auth/XSS/DOM regression | any | full rollback to keyword + incident |
| Memory growth | > 15% over baseline | force watchdog restart |
| Empty-cell fallback failure rate | sustained > 20% | rollback to candidate, investigate |

Implemented in `tools/campaign_watchdog.py` as automatic actions, not manual judgment.

### Phase F — Document + propagate (1h)

- Write measured numbers into `docs/HYBRID_RETRIEVAL.md`
- Update `CHANGELOG.md` Unreleased
- Update `CURRENT_STATUS.md`
- Mark precondition met in `memory/project_hex_solver_scaling.md`
- Update `docs/cells/INDEX.md` with live FAISS version manifest

---

## 3. MAGMA retrieval trace (final schema)

Every routed query writes:

```yaml
retrieval_trace:
  trace_id: <uuid>
  query_hash: <sha256(query_text)>
  timestamp_utc: <iso>
  route_mode: shadow | candidate | authoritative
  embedding_hot_path_taken: bool
  embedding_model: nomic-embed-text:v1.5
  embedding_dim: 768
  embedding_latency_ms: <float>
  keyword_cell: <cell_id>
  keyword_confidence: <float>
  centroid_cells_top3:
    - {cell: thermal, score: 0.87}
    - {cell: energy, score: 0.71}
    - {cell: safety, score: 0.42}
  searched_cells:
    - {cell: thermal, ring_depth: 0, non_empty: true, faiss_version: 2026-04-23T15:30:00Z-3a4f}
    - {cell: energy, ring_depth: 1, non_empty: true, faiss_version: 2026-04-23T15:30:01Z-7b2e}
  candidate_doc_ids:
    - {doc_id: heating_cost#canonical_fi, score: 0.91, view_type: canonical_fi}
    - {doc_id: heat_pump_cop#canonical_fi, score: 0.74, view_type: canonical_fi}
  chosen_canonical_solver_id: heating_cost
  chosen_score: 0.91
  fallback_stage: local_cell_faiss | ring1_faiss | ring2_faiss | global_chroma | llm_fallback | no_answer
  faiss_manifest_version: 2026-04-23T16:00:00Z-coherent-set-A
  empty_cell_miss: false
  keyword_centroid_disagreement: false
  question_frame:
    desired_output: numeric
    comparator: null
    negation: false
  verifier_result: pass | fail | not_run
  quality_tier: GOLD | SILVER | BRONZE | QUARANTINE | null
  total_routing_latency_ms: <float>
```

Without these fields, hybrid retrieval **weakens** MAGMA (GPT-5's warning).

---

## 4. Risk re-assessment (post-review)

| # | Risk | Original assessment | Refined assessment | Mitigation |
|---|---|---|---|---|
| 1 | Cell mis-assignment locks embedding to wrong cell | Medium impact | **HIGH impact** (GPT-5) | §1.2 keyword + centroid parallel, log disagreement |
| 2 | No-GPU edge breaks "<1ms" promise | not flagged | **CRITICAL** (Grok) | §1.4 lazy embedding hot path |
| 3 | Hex topology adds complexity for no gain | Low | **MEDIUM** (Grok + Gemini) | §1.5 measure vs flat baseline; abandon if not worth it |
| 4 | FAISS read-during-write race | Low | Medium | §1.9 RCU + staging + atomic swap |
| 5 | Multilingual asymmetry (FI/EN) | Medium | Medium | §1.7 multi-view embeddings |
| 6 | Negation/question-type misrouting | not flagged | Medium (GPT-5) | §1.8 question-frame parser |
| 7 | Cell version drift (8 cells, 8 versions) | not flagged | Medium (Claude) | §1.10 per-cell version manifest |
| 8 | Embedding model deprecation | not flagged | Low for now | §1.11 migration tool scaffold |
| 9 | Keyword-truth bias in shadow validation | not flagged | High (GPT-5) | §1.6 three independent ground truth sources |
| 10 | Memory growth from loaded indices | Low | Low (Gemini: <120 MB at 40k vectors) | Existing watchdog covers this |

---

## 5. Implementation order (revised)

```
Phase A: Pre-flight                                    30 min
Phase B.0: Multi-view axiom backfill tool             3 h
Phase B.1: Cell centroid computation                   1 h
Phase B.2: Three-way shadow runner                     2 h
Phase B.3: Hex manifest tool (versioning + rollback)   1 h
Phase B.4: Embedding migration scaffold (placeholder)  30 min
Phase B.5: Question-frame parser + tests               2 h
Phase C: Oracle validation + 3-way shadow             8 h (overnight)
        ↓ DECISION GATE: hex / flat / abandon
Phase D-1: Candidate mode                              24 h (live)
        ↓ DECISION GATE: hybrid_unique_correct ratio
Phase D-2: Authoritative mode                          10 min
Phase E: 24 h hardened measurement with sharp kills    24 h (live, automated)
Phase F: Document + propagate                          1 h
                                                       ─────
Total active work:                                     ~10 h
Total elapsed (with overnight + 24h windows):          ~3 days
```

## 6. Decision authority matrix

| Decision | Who decides | Evidence required |
|---|---|---|
| Phase A → B | Auto if A pass | A pre-flight report |
| Phase B → C | Auto if all B tests pass | B test results |
| Phase C → D-1 vs abandon-hex vs don't-enable | **Jani** | Three-way shadow report + this plan §1.5 gate matrix |
| Phase D-1 → D-2 | Auto if 3:1 ratio + 0 regressions | Phase D-1 report after 24h |
| Rollback during Phase E | Watchdog automatic | Any kill signal hit |
| Re-attempt after rollback | **Jani** | Incident root-cause document |

## 7. What is explicitly NOT in this plan

Reviewers raised these but they are **out of scope for this activation**:

1. **Cross-encoder reranker** (Gemini: bge-reranker-base SOTA pattern). Acknowledged as future work in `memory/project_hex_solver_scaling.md` Week N+1. Not now because it adds another network hop.

2. **Replacing hex with flat-only architecture** (Grok). May happen if Phase C says so, but is a follow-up plan, not this one.

3. **Wait until 200 solvers** (Grok). Tradeoff: waiting blocks the scaling roadmap from being measurably real. Acceptable counter: do shadow now (no risk), only enable after Phase C proves value.

4. **MicroModel V3 LoRA classifier** as the primary router. Memory plan Week 5+, after a few months of production data accumulation.

5. **Subdivision of cells** (Level 1+ in memory plan). Premature; current 14 solvers don't fill 8 cells yet, no need to subdivide.

---

## 8. Success criteria for "this plan worked"

After full execution (Phase A-F complete):

1. ✅ Three independent ground truth sources exist and are checked into git
2. ✅ Phase C three-way shadow report exists with measured numbers (not estimates)
3. ✅ Decision is documented: hex / flat / don't-enable, with rationale linked to data
4. ✅ If hex enabled: 24h authoritative mode passed with 0 kill-signal triggers
5. ✅ MAGMA retrieval traces include all §3 fields for 100% of routed queries
6. ✅ FAISS version manifest exists and rollback works (tested via `tools/hex_manifest.py rollback --dry-run`)
7. ✅ No regression in: 400h campaign green hours rate, XSS/DOM/auth metrics, memory growth rate
8. ✅ `docs/HYBRID_RETRIEVAL.md` updated with measured numbers replacing estimates
9. ✅ `memory/project_hex_solver_scaling.md` precondition `hybrid_retrieval.enabled: true` marked done

If any of these is incomplete after Phase F, the plan **did not succeed** even if the system technically runs hybrid retrieval. Honesty culture requires marking it incomplete.

---

## 9. Where credit goes

- **Activation modes (shadow/candidate/authoritative)**: GPT-5
- **Cell-assignment honesty + centroid parallel**: GPT-5
- **Empty cell policy**: GPT-5
- **Multi-view embeddings + canonical_solver_id dedup**: GPT-5
- **Three independent ground truth sources**: GPT-5
- **Negation/question-type parser**: GPT-5
- **FAISS RCU/staging/atomic swap pattern**: GPT-5 + Gemini 2.5 Pro
- **MAGMA retrieval trace schema**: GPT-5 (all 18 sections of metrics doc)
- **No-GPU lazy embedding hot path**: Grok 4 (CRITICAL — only Grok flagged)
- **Sharp kill signals (p50 +8ms, fallback +2pp, hit rate decreases)**: Grok 4
- **One-line ring-expansion-useful-rate metric**: Grok 4
- **Hex vs flat A/B test in Phase C**: Gemini 2.5 Pro
- **`/api/embed` (not `/api/embeddings`), batch 16-32**: Gemini 2.5 Pro
- **`os.replace` for Windows-atomic**: Gemini 2.5 Pro
- **`asyncio.to_thread(search, ...)`**: Gemini 2.5 Pro
- **IndexFlatL2 stays valid until 50k/cell**: Gemini 2.5 Pro
- **Per-cell version manifest + atomic rollback**: Claude (no reviewer flagged)
- **Embedding model migration scaffold**: Claude (no reviewer flagged)
- **Decision authority matrix**: Claude (synthesis)

---

## 10. Final pre-implementation question for Jani

This plan is implementation-ready. The only thing missing is **your approval to proceed with Phase A**. After Phase C runs, there will be another decision gate where you choose hex / flat / don't-enable based on measured data.

Optional: send this plan to GPT-5 once more for a "does this fully address my review?" pass before starting implementation. That's another 30 min of your time and probably catches any synthesis mistakes I made.

Approval format:
- "Aloita Phase A" — start implementation, you'll see Phase C results before any production change
- "Lähetän vielä GPT:lle" — wait, send refined plan for one more review round
- "Tee X muutos ensin" — specific change request before approving

Awaiting your call.
