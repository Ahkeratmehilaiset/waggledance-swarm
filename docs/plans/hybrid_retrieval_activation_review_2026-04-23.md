# Plan: Activate `hybrid_retrieval` in WaggleDance — Multi-AI Review Request

**Author:** Jani Korpi (with Claude Sonnet 4.6 assistance)
**Date:** 2026-04-23
**Status:** Pre-implementation review
**Recipients:** GPT-5 (or strongest available OpenAI model), Grok 4 (xAI), Gemini 2.5 Pro (Google)

---

## How to use this document

1. Send the **CONTEXT** + **PLAN** + **RISKS** + **ALTERNATIVES** sections to all three AIs.
2. Then ask each AI the **questions in their dedicated section** below.
3. Collect responses.
4. Send all three responses back to me (Claude Sonnet 4.6) and I will synthesize the strongest critique into a final implementation plan.

The point: **1 hour planning saves 10 hours doing**. Different AIs have genuinely different blind spots. GPT tends to be conservative and complete; Grok challenges premises; Gemini knows libraries deeply. Triangulating their critique catches more than any single review.

---

## Context (read this first, all reviewers)

### Project

**WaggleDance Swarm AI** — local-first multi-agent runtime, ~75 agents, solver-first routing with LLM fallback only when no deterministic solver matches. License Apache 2.0 + BUSL 1.1. Repo: `Ahkeratmehilaiset/waggledance-swarm`. Currently shipped: v3.5.7 (2026-04-12 "Honest Hologram Release"). Five v3.5.7 days post-ship hardening + 400h UI gauntlet campaign in progress (~55% done as of writing, 0 product defects).

### What is "solver-first routing"

A user query enters; the router classifies it; if a deterministic solver matches (e.g. `heating_cost` for "paljonko lämmitys maksaa") it runs in ~0.17 ms and returns a numerical answer with full audit trail. If nothing matches, fall through to LLM (Ollama, optionally Gemma 4 dual-tier). Currently 14 symbolic solvers exist (`configs/axioms/<domain>/<model_id>.yaml` — declarative formula+variable specs, not code).

### What is the "hybrid retrieval"

Built but **disabled** layer between query embedding and solver lookup. Each query is embedded via Ollama `nomic-embed-text`. Each solver lives in one of 8 hex cells (`general / thermal / energy / safety / seasonal / math / system / learning`) with a 6-neighbor adjacency graph. A query routes to its nearest cell, then nearest solver in that cell, with ring-1 and ring-2 neighbor expansion if local match score is too low. The architecture references hexagonal lattices in cortex minicolumns and beehive comb topology — same geometric primitive: 6 neighbors, no gaps in coverage.

### Current state of `hybrid_retrieval` (concrete facts)

- `configs/settings.yaml`:
  ```yaml
  hybrid_retrieval:
    enabled: false             # ← we want to flip this to true
    ring2_enabled: false
    min_score: 0.35
    sufficient_score: 0.7
  ```
- `core/faiss_store.py`: `FaissCollection`, `FaissRegistry`, `SearchResult` — vector store wrappers around `faiss-cpu` 1.13.2.
- `waggledance/bootstrap/container.py::faiss_registry` cached_property: returns `None` if `faiss-cpu` is missing (CI guard, fixed 2026-04-20 commit `b21548d`); otherwise instantiates `FaissRegistry`.
- `waggledance/application/services/hybrid_retrieval_service.py::HybridRetrievalService.retrieve()` short-circuits to global ChromaDB when `enabled=False`. When `enabled=True`, runs local-FAISS-cell → ring1-FAISS-neighbors → global-ChromaDB → LLM-fallback.
- `waggledance/application/services/hybrid_backfill_service.py::HybridBackfillService.run()` exists for populating cells from CASE trajectories (post-execution evidence). It expects a `case_store` source — does NOT directly ingest from `configs/axioms/*.yaml`.
- `waggledance/core/hex_cell_topology.py::HexCellTopology.assign_cell(intent, query)`: assigns cells via (a) intent-string lookup, (b) keyword overlap fallback. Embedding-based assignment is NOT yet wired into the topology layer — the topology takes (intent, query_text) as input.

### Existing supporting infrastructure done in last 24h

- `tools/cell_manifest.py` — generates per-cell MANIFEST.md / manifest.json from current axiom library. First run shows 14 solvers across the 8 cells; `seasonal` and `learning` cells are empty.
- `waggledance/core/learning/solver_hash.py` — canonical sha256 over (formulas, variables, conditions); 17 tests pass.
- `tools/campaign_watchdog.py` — keeps gauntlet server alive; 60s health poll + 12h preventive restart.
- `tools/campaign_auto_commit.py` — regenerates Phase 6/7/9 reports + commits + pushes every 30 min.

### Why we want to enable hybrid retrieval NOW

Two reasons:

1. **Current keyword-based routing breaks at ~500-1000 solvers.** Adding solver #501 with overlapping keywords to existing solvers causes routing collisions. The repo has a documented scaling roadmap (`memory/project_hex_solver_scaling.md`) targeting 14 → 10k+ solvers in 24 months. Embedding-based routing is the precondition for moving past 500.
2. **Hybrid retrieval is built but proves nothing while disabled.** The previous v3.5.0 proof run (`docs/PROOF_RUN_REPORT_v350.md`, 2026-04-02) showed `hybrid ON` reduced p50 from 9055 ms → 4231 ms (-53%) on a 30h soak. But that test was on case-trajectory backfill, not axiom backfill. For the scaling story to be credible, we need it on for axiom routing too, with measurable numbers from the running 400h campaign.

---

## Plan

### Phase A — Pre-flight (30 min)

| Step | Action | Pass criterion |
|---|---|---|
| A.1 | `pip list | grep faiss-cpu` (server venv) | `faiss-cpu==1.13.2` installed |
| A.2 | `curl -X POST http://ollama:11434/api/embeddings -d '{"model":"nomic-embed-text","prompt":"test"}'` | 200 OK with vector array |
| A.3 | Inspect `tools/cell_manifest.py` output for cell-coverage gaps | Each used cell has ≥1 solver OR is intentionally empty |
| A.4 | Confirm `HybridRetrievalService.embed_fn` is non-None at startup | DI logs `embed_fn=<callable>` |
| A.5 | Backup `configs/settings.yaml` and `data/faiss/` (if exists) | Files copied to `backup/2026-04-23/` |

### Phase B — Build the axiom backfill tool (~3h)

`HybridBackfillService` only ingests from CASE trajectories. We need a separate tool to ingest from axioms (the deterministic-solver library) so they appear in FAISS:

```
tools/backfill_axioms_to_hex.py
```

Pseudocode:
```python
for axiom_path in glob("configs/axioms/*/*.yaml"):
    axiom = yaml.safe_load(open(axiom_path))
    text = " ".join([
        axiom["model_id"],
        axiom.get("model_name", ""),
        axiom.get("description", ""),
        " ".join(f["name"] for f in axiom.get("formulas", [])),
        " ".join(axiom.get("variables", {}).keys()),
    ])
    cell_id = hex_topology.assign_cell(intent="symbolic", query=text).cell_id
    vec = embed_fn(text)
    faiss_registry.get_or_create(f"cell_{cell_id}").add(
        doc_id=axiom["model_id"],
        text=text,
        vector=vec,
        metadata={"source": "axiom", "domain": str(Path(axiom_path).parent.name),
                  "axiom_file": str(axiom_path), "model_id": axiom["model_id"]},
    )
```

Tests required: 8 sanity tests (idempotency, cell assignment determinism, embedding error handling, missing axiom field handling, dry-run mode, `--filter-domain`, hash dedup against `solver_hash.canonical_hash`, no double-add on rerun).

### Phase C — Shadow validation (~6h, can run unattended overnight)

Don't enable in production immediately. Run side-by-side:

1. Sample 1000 most recent queries from `docs/runs/ui_gauntlet_400h_20260413_092800/hot_results.jsonl`
2. For each query:
   - Get current routing decision (keyword-based, what production does today)
   - Get hypothetical hybrid routing decision (with `enabled=true`)
3. Record:
   - Routing latency (keyword: ~0.001 ms; hybrid: TBD ms)
   - Routed-to-solver vs routed-to-LLM ratio
   - Top-1 / top-5 cell assignment agreement
4. Output: `docs/runs/hybrid_shadow_2026-04-23.md`

Pass criterion: hybrid agrees with keyword routing on ≥ 80% of cases AND opens new correct routings on ≥ 5% (cases keyword routing missed but hybrid finds a relevant solver).

### Phase D — Production enable (~10 min)

1. Edit `configs/settings.yaml`: `hybrid_retrieval.enabled: true`.
2. Restart server via watchdog: `kill <server_pid>` (watchdog auto-restarts within 60 s with new config).
3. Verify: `curl localhost:8002/api/status | jq .hybrid_retrieval` shows `enabled=true, queries=0`.
4. Watch first 100 production queries via `tail -f hot_results.jsonl`.

### Phase E — 24h measurement (~1 day)

Compare 24h-before vs 24h-after on the running 400h campaign:

| Metric | Hypothesis |
|---|---|
| Median routing latency | hybrid ≤ keyword + 5 ms |
| LLM fallback rate | hybrid < keyword by ≥ 5 percentage points |
| Solver hit rate | hybrid > keyword by ≥ 10 percentage points |
| XSS hits | unchanged at 0 |
| DOM breaks | unchanged at 0 |
| Backend 5xx | unchanged |
| Server memory growth rate | within 10% of pre-enable rate |

Output: `docs/runs/hybrid_24h_validation_2026-04-24.md`. If pass → keep on. If fail → flip back to `false`, file incident, root-cause.

### Phase F — Document + propagate (~1h)

- Update `docs/HYBRID_RETRIEVAL.md` with measured numbers.
- Update `CHANGELOG.md` Unreleased block.
- Update `CURRENT_STATUS.md`.
- Note in `memory/project_hex_solver_scaling.md` that the precondition is met → unblock Week 3 (`propose_solver.py`).

---

## Risks (ranked by impact × probability)

| # | Risk | Impact | Probability | Mitigation |
|---|---|---|---|---|
| 1 | Embedding model latency spikes when many cells loaded simultaneously | Medium | Medium | Phase E watch; deque of last 100 latencies, alert on p95 > 50ms |
| 2 | Cell mis-assignment because heuristic keyword classifier in `hex_cell_topology` doesn't match embedding clusters | High | Medium | Phase C shadow catches this; fallback: re-classify cells from embedding centroids |
| 3 | FAISS index disk size grows unboundedly as Dream Mode adds case trajectories | Medium | Low | Already bounded via `case_builder._cases` 5000→2500 cap (commit 449fda7) |
| 4 | Server cold-start time increases due to FAISS index loading | Low | High | Acceptable; restart frequency is 12h via watchdog |
| 5 | Ollama embed endpoint becomes a hard dependency for routing (vs. graceful degrade) | High | Medium | `HybridRetrievalService` already short-circuits when `embed_fn` returns None; verify path explicitly in test |
| 6 | Axiom embeddings drift over time as model_id/name/description fields are edited | Low | High | Idempotent backfill: re-run on every commit that touches `configs/axioms/`, hash-dedup |
| 7 | Production query distribution differs from training distribution; hybrid worse on long tail | Medium | Medium | Phase E pass criterion includes p95, not just median |

---

## Alternatives considered (and why rejected)

1. **Pure embedding routing (no cells, just one big FAISS index of all solvers).** Rejected: doesn't scale to 10k+ solvers; the cell boundary serves both as a routing optimization (smaller index = faster) and as the natural expansion unit for the planned hex-subdivision (cell → 6 sub-cells).

2. **Train a classifier model (MicroModel V3 LoRA) on (query, solver_id) pairs.** Rejected for now (deferred): requires 4h GPU training, requires N≥1000 labeled pairs, no infra to label. Will revisit when production has accumulated enough Dream Mode-validated trajectories.

3. **Improve keyword routing with stemming + n-grams.** Rejected: a band-aid that buys maybe 2× scaling (to ~1000 solvers) but kills the 10k+ scaling roadmap and adds maintenance burden (per-language stemmer rules).

4. **Stay on keyword routing forever.** Rejected: caps the library at ~500 solvers, contradicts the published 24-month scaling story in `CURRENT_STATUS.md` and `memory/project_hex_solver_scaling.md`.

5. **Use a different vector store (Milvus / Qdrant / pgvector).** Rejected: the project is local-first, single-machine; FAISS is already integrated and tested; switching adds dependency for no current benefit.

6. **Outsource embeddings to OpenAI (`text-embedding-3-large`).** Rejected: contradicts the local-first, zero-cloud brand. Ollama `nomic-embed-text` runs on the same machine.

---

## Questions FOR GPT-5 (architecture / correctness reviewer)

GPT, your strength: structured analysis, edge cases, theoretical correctness, second-order effects.

1. **Cell assignment honesty.** The current `hex_cell_topology.assign_cell()` uses a keyword heuristic. The hybrid retrieval then uses embeddings within the cell. This means **cell assignment** itself is keyword-based but **intra-cell ranking** is embedding-based. Is this a coherent architecture, or does it negate most of the embedding benefit? If incoherent, what's the minimal change to fix it (e.g. embedding-based cell assignment with cell centroids, or cell expansion when intra-cell match score < threshold)?

2. **Failure mode under empty cells.** Two of 8 cells (`seasonal`, `learning`) are currently empty. When a query routes to an empty cell, what should happen? The current spec says "ring-1 fallback", but that is a feature designed for "few solvers", not "zero". Is this safe? Should we forbid routing to empty cells? Should we auto-merge empty cells into their largest neighbor?

3. **Symmetric vs asymmetric quality.** When the user prompts "paljonko lämmitys maksaa" and the matching solver's description is "Cottage heating cost estimate", they share the embedding vocabulary "lämmitys" / "heating cost". But what about Finnish queries where the solver only has English text? Should we pre-translate solver descriptions to multiple languages, embed each, and store under the same doc_id?

4. **Shadow validation correctness.** Phase C compares keyword routing vs hybrid routing on past queries. But past queries' "ground truth" is what keyword routing ALREADY decided. This biases the comparison toward keyword routing being "right". How would you redesign the validation so it doesn't have this self-fulfilling bias? Suggest a third source of truth.

5. **Race condition between FAISS index updates and concurrent reads.** If Dream Mode adds a new training pair to a cell's FAISS index while an HTTP request is reading from it, what's the FAISS thread safety story? Is read-during-write safe?

6. **The "negation problem".** If a user asks "is the heating cost above 50 EUR" — embedding-similar to "what is the heating cost" — both route to `heating_cost` solver. But the question semantics are different (boolean vs numeric). Does the architecture handle this, or do we need a question-type classifier ahead of solver selection?

---

## Questions FOR GROK 4 (challenge-the-premise reviewer)

Grok, your strength: heretical questions, first-principles analysis, performance math.

1. **Is the hex topology a real abstraction or a metaphor?** The README invokes beehives and cortical minicolumns. Honest answer: does the 6-neighbor structure provide measurable benefit over a flat embedding-distance model + greedy nearest-K retrieval? Or are we paying complexity tax for a biologically-inspired design that adds nothing to the math?

2. **Premature scaling.** The plan justifies hybrid_retrieval by the "10k solver scaling roadmap". But the actual library is 14 solvers. Is enabling hybrid retrieval at this scale solving a 24-month-future problem at the cost of current production stability? Would it be smarter to wait until library reaches, say, 200 solvers (10x current) before turning on?

3. **Real benchmark.** I claim the v3.5.0 proof run showed p50 9055 → 4231 ms with hybrid on. But that was case-trajectory backfill (post-execution memory), not axiom backfill (deterministic-solver library). Is it credible to extrapolate that 53% latency win to the axiom case? What's the BASE rate for embedding-based routing latency that I should expect?

4. **The brand contradiction.** WaggleDance is "local-first, zero cloud". But the embedding model `nomic-embed-text` running in Ollama still calls a model loaded into GPU memory. If the user's machine doesn't have a GPU (Raspberry Pi deployment promised in `--preset=raspberry-pi-iot`), embedding becomes the bottleneck. Have I budgeted for the no-GPU path? What does CPU embedding latency look like for nomic-embed?

5. **The simplest possible thing.** If you were starting from scratch with the same goal (scale solver library to 10k while keeping latency < 30 ms), would you build this hex+FAISS+ring1+ring2+ChromaDB stack? Or is there a simpler design that is being missed because the code is already written?

6. **What's the kill signal?** If hybrid retrieval is performing worse than keyword routing in production, what is the EXACT metric that would force a rollback? Is "5% LLM fallback increase" sharp enough, or am I being vague to give myself optionality?

---

## Questions FOR GEMINI 2.5 PRO (implementation / library reviewer)

Gemini, your strength: code/library knowledge, Google-scale patterns, practical implementation.

1. **FAISS index type for this scale.** Each cell will have 5-50 solvers initially, growing to 200-1000 per cell at scale. What FAISS index type is appropriate at each scale? `IndexFlatL2`? `IndexIVFFlat`? `IndexHNSWFlat`? When should we switch? What's the cutover threshold?

2. **Ollama nomic-embed batch size.** When backfilling 14 → 1000 axioms, should we batch-embed? What's the optimal batch size for `nomic-embed-text` on a typical Ollama deployment? Are there known throughput cliffs?

3. **Embedding dimensions.** `nomic-embed-text` returns 768-dim vectors. For 8 cells × 1000 solvers × 768 floats × 4 bytes = ~24 MB total — trivial. But once Dream Mode adds case trajectories (1000s per cell), this grows. At what point do we need PQ quantization or `IndexIVFPQ`?

4. **FAISS persistence on Windows.** The deployment runs on Windows (i7-12850HX, Python 3.13). FAISS persistence (`write_index` / `read_index`) is documented mostly on Linux. Are there Windows-specific gotchas (file locking, mmap behavior, pickling) we should anticipate?

5. **HybridRetrievalService.retrieve() concurrency.** FastAPI async handlers will call this concurrently (10s of QPS during gauntlet). Looking at `core/faiss_store.py::FaissCollection.search()`, is there a GIL bottleneck that limits real concurrency? Should `search()` be wrapped in `asyncio.to_thread` to avoid blocking the event loop?

6. **Comparison with state-of-the-art.** Companies running production embedding-based routing at the ~1000s scale (early-stage Anthropic, Hugging Face, Vespa-based startups) — are they doing anything fundamentally different we should adopt? Is "hex cells + ring1 + ring2 + global" architecturally outdated vs. e.g. ColBERT, learned re-rankers, or cross-encoders?

7. **Idempotency.** The backfill tool should be safe to rerun without duplicating entries. Looking at `FaissRegistry`/`FaissCollection`, is there a built-in dedup-by-doc_id, or do we need `solver_hash.canonical_hash` to enforce that ourselves?

---

## Decision template (for after responses)

After collecting GPT/Grok/Gemini responses, I will fill this in:

| Question | GPT answer | Grok answer | Gemini answer | Synthesized resolution |
|---|---|---|---|---|
| (e.g. cell assignment honesty) | … | … | … | … |
| (e.g. premature scaling) | … | … | … | … |
| … | … | … | … | … |

Final implementation plan will then differ from THIS plan in concrete ways — expect revisions to:
- Phase C shadow validation methodology
- Risk #2 (cell mis-assignment) mitigation
- FAISS index type choice
- Whether to enable now vs. wait

---

## Appendix: Key files and SHAs (for reviewer reference)

```
core/faiss_store.py                                                @ a416ec4
waggledance/application/services/hybrid_retrieval_service.py       @ a416ec4
waggledance/application/services/hybrid_backfill_service.py        @ a416ec4
waggledance/bootstrap/container.py                                 @ a416ec4 (faiss_registry guard)
waggledance/core/hex_cell_topology.py                              @ a416ec4
waggledance/core/learning/solver_hash.py                           @ a416ec4 (new)
tools/cell_manifest.py                                             @ a416ec4 (new)
configs/settings.yaml                                              @ a416ec4 (hybrid_retrieval.enabled: false)
configs/axioms/cottage/honey_yield.yaml                            @ a416ec4 (sample axiom)
docs/PROOF_RUN_REPORT_v350.md                                      @ a416ec4 (prior hybrid eval)
docs/HYBRID_RETRIEVAL.md                                           @ a416ec4
memory/project_hex_solver_scaling.md                               @ local memory (saved 2026-04-22)
```

Repo: https://github.com/Ahkeratmehilaiset/waggledance-swarm

End of plan. Send to GPT-5, Grok 4, Gemini 2.5 Pro. Bring responses back to Claude Sonnet 4.6 for synthesis.
