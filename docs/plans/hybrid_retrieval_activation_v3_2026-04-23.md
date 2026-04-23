# Hybrid Retrieval Activation Plan — v3 (Implementation-Ready)

**Author:** Jani Korpi + Claude Sonnet 4.6 (synthesis)
**Date:** 2026-04-23
**Supersedes:**
- `docs/plans/hybrid_retrieval_activation_review_2026-04-23.md` (v1, original draft)
- `docs/plans/hybrid_retrieval_activation_refined_2026-04-23.md` (v2, multi-AI synthesis)
**Reviews integrated:**
- v1 → v2: GPT-5 first pass + Grok 4 + Gemini 2.5 Pro + Claude additions
- v2 → v3: GPT-5 second pass (`docs/plans/GPT_response2.txt`)

**Status:** Implementation-ready, awaiting one final pre-Phase-A confirmation.

---

## 0. Why v3 differs from v2

GPT-5 round-2 review caught **one critical missed amendment** plus **seven concrete protocol details** v2 left ambiguous. The single most important change:

> **Source-side axiom placement was still keyword-based in v2.** We fixed
> query-side cell-honesty (centroid + keyword parallel) but the backfill
> tool's `cell = topology.assign_cell(intent="symbolic", query=axiom["model_name"])`
> means solvers can be PERMANENTLY misplaced in the wrong cell. Hidden
> failure mode after fixing the obvious one. v3 mandates declared
> `cell_id` + `affinity_cells` in axiom YAML AND a centroid-disagreement
> audit step in backfill.

Other v3 additions versus v2:

| New section | Source | Why critical |
|---|---|---|
| §1.12 Embedding determinism gate | GPT round-2 | Without proof, manifest checksums based on raw vectors are meaningless |
| §1.13 Append-only delta ledger | GPT round-2 | Coordinates Dream Mode + axiom backfill; without it, races on staging |
| §1.14 Per-query coherent snapshot | GPT round-2 | Prevents `cell_thermal@v3 + cell_energy@v4` inconsistent reads |
| §1.15 Embedding cache | GPT round-2 | 90%+ embedding cost saving on 400h gauntlet |
| §1.16 Disaster recovery extended invariant | GPT round-2 | Axioms alone are NOT enough once Dream Mode contributes |
| §1.17 CSI split (strict vs progress) | GPT round-2 | Multiplicative form hides progress when one term zeros |
| §1.18 Solver output schema | GPT round-2 | Negation parser useless without typed output fields |
| §1.19 Phase C execution scope | GPT round-2 | Full 1000-query 3-way on workstation; edge gets smoke variant |

v2 → v3 is NOT a re-architecture; it is **protocol hardening** of a sound v2 architecture.

---

## 1. Architectural decisions (all sections, v3 additions marked)

### 1.1 Activation modes (GPT-5, with GPT round-2 wording fix)

```yaml
hybrid_retrieval:
  loaded: true             # service is loaded; traces emitted; routing NOT necessarily authoritative
  mode: shadow             # shadow | candidate | authoritative
  ring2_enabled: false
  min_score: 0.35
  sufficient_score: 0.7
  embedding_hot_path: false
```

Renamed `enabled` → `loaded` to remove the "enabled = authoritative" misreading risk GPT round-2 flagged.

| Mode | Behavior | When to use |
|---|---|---|
| `shadow` | Hybrid computes candidates, MAGMA traces both, production uses old route | Phase C |
| `candidate` | Hybrid candidate visible to verifier, can be overridden by old route | Phase D-1 |
| `authoritative` | Hybrid chooses production solver path | Phase D-2 (only after gates pass) |

### 1.2 Cell assignment honesty — query-side AND source-side (GPT-5, GPT round-2)

**Query-side (was in v2):** Compute BOTH `keyword_cell` and `centroid_cell_top3`. Search candidate-cell union: keyword_cell ∪ centroid_top3 ∪ ring1(keyword_cell) ∪ ring1(centroid_top1). Log `keyword_centroid_disagreement` to MAGMA.

**Source-side (NEW in v3, GPT round-2 catch):**

Every axiom YAML must declare its cell explicitly:

```yaml
# configs/axioms/cottage/heating_cost.yaml
model_id: heating_cost
cell_id: thermal              # NEW — required for v3 axioms
affinity_cells: [energy]      # NEW — optional, list of secondary cells the solver bridges
model_name: "Cottage heating cost estimate"
description: "..."
formulas:
  - ...
```

Backfill audit step (NEW B.0 substep):

```python
for axiom in axioms:
    declared = axiom.get("cell_id")
    keyword = topology.assign_cell(intent="symbolic", query=axiom["model_name"]).cell_id
    centroid = assign_cell_embedding(embed(axiom_text))[0]  # top-1 centroid

    if declared is None:
        # legacy axiom — must be patched before backfill proceeds
        FAIL(f"axiom {axiom['model_id']} missing cell_id declaration")

    if declared != keyword and declared != centroid:
        # human-declared cell disagrees with both heuristics — flag for review
        WARN_REQUIRES_REVIEW(axiom["model_id"], declared, keyword, centroid)

    # Use the declared cell as authoritative; record disagreements for audit
    record_axiom_placement(
        axiom_id=axiom["model_id"],
        used_cell=declared,
        keyword_cell=keyword,
        centroid_cell=centroid,
        affinity_cells=axiom.get("affinity_cells", []),
    )
```

Axioms without declared `cell_id` block the backfill (forced honesty: humans must commit).

### 1.3 Empty cell policy (GPT-5, unchanged from v2)

Empty cells are valid semantic targets but NOT searched locally:

```python
if target_cell.is_empty:
    record_empty_cell_miss(cell_id, query_hash, intent, centroid_score)
    search_non_empty_ring1_neighbors()
    if no_candidate_above_threshold:
        search_global_chroma()
    if still_no_candidate:
        fallback_to_llm()
# NO auto-merge into largest neighbor
```

### 1.4 No-GPU lazy embedding hot path (Grok 4, unchanged from v2)

Embedding is NOT the hot path. Keyword routing remains the hot path. Embed only on miss/low-confidence:

```python
def route(query):
    keyword_route = router_v2.route(query)        # ~0.1 ms always
    if keyword_route.confidence >= sufficient_score:  # 0.7
        return keyword_route                       # fast path; no embedding
    return hybrid_retrieval.retrieve(query, fallback_keyword=keyword_route)
```

Preserves "<1 ms solver-first" promise on Raspberry Pi where embedding is 80-180 ms.

### 1.5 Hex vs flat A/B test in Phase C (Gemini 2.5 Pro, unchanged)

Phase C runs three architectures side-by-side: keyword, flat single-FAISS, hex+rings. Decision gate after Phase C explicitly allows abandoning hex if flat wins.

### 1.6 Three independent ground truth sources (GPT-5 + GPT round-2 nuance)

**A. Axiom oracle set** — for each solver, hand-write 20-50 positive + 20 negative queries at `tests/oracle/<solver_id>.yaml`.

**B. Verified GOLD/SILVER CaseTrajectory subset.** Updated v3 filter to address GPT round-2 nuance:

```python
trajectories = [t for t in case_store
                 if t.route_type == "solver"
                 and t.verifier_pass == True
                 and t.quality_tier in ("GOLD", "SILVER")
                 # NEW v3: record router/verifier version so old trajectories
                 # don't poison the truth set after a router change
                 and t.original_router_version >= MIN_TRUSTED_ROUTER_VERSION
                 and t.verifier_version >= MIN_TRUSTED_VERIFIER_VERSION]
```

**C. Blind adjudication set** — 200-500 queries blindly labeled by separate AI/human at `tests/oracle/blind_adjudication.yaml`.

### 1.7 Multi-view embeddings (GPT-5 + GPT round-2 test addition)

Per solver: 4-5 views with same `canonical_solver_id`, top-k dedup by canonical_solver_id.

NEW v3 test: `test_multi_view_dedup_top_k` — assert that a single solver with 5 views CANNOT occupy all 5 top-5 slots after dedup. Required test added to B.0 backfill test list.

### 1.8 Negation/question-type parser + solver output schema (GPT-5 + GPT round-2)

Question-frame parser finds answer shape (numeric / boolean / explanation / diagnosis / optimization). Embedding finds solver. NEW v3: solver output schema is required so postprocessor knows what to compare:

```yaml
# configs/axioms/cottage/heating_cost.yaml — extended
model_id: heating_cost
cell_id: thermal
solver_output_schema:
  primary_value:
    name: cost_eur
    type: number
    unit: EUR
  comparable_fields:
    - name: cost_eur
      unit: EUR
    - name: kwh
      unit: kilowatt-hour
    - name: temperature_diff
      unit: celsius
  output_mode: numeric        # numeric | classification | trace_only
formulas: [...]
variables: [...]
```

Without this, parser detects `"is cost > 50 EUR?"` but postprocessor can't pick which numeric field. Required for all axioms before Phase D-2 (authoritative).

### 1.9 FAISS index safety + per-query snapshot (GPT-5 + Gemini 2.5 Pro + GPT round-2)

**v2 covered:** RCU/staging/atomic swap pattern, `os.replace(tmp, final)` for Windows-atomic, per-cell `version_manifest.json`.

**NEW v3:** Per-query coherent snapshot rule (§1.14 below) and append-only delta ledger (§1.13 below).

### 1.10 Per-cell version manifest + atomic rollback (Claude, unchanged)

```yaml
# data/faiss_live/manifest.json
manifest_version: 2026-04-23T16:00:00Z-coherent-set-A
cells:
  thermal:    {version: 2026-04-23T15:30:00Z-3a4f, status: live}
  energy:     {version: 2026-04-23T15:30:01Z-7b2e, status: live}
  ...
delta_ledger_high_water_mark: 1842    # NEW v3 — see §1.13
```

`tools/hex_manifest.py rollback --to-version <id>` swaps all cells back atomically.

### 1.11 Embedding model migration scaffold (Claude, unchanged)

`tools/migrate_embedding_model.py` placeholder for v3.6 / nomic-v2 migration. Documents the procedure now.

### 1.12 Embedding determinism gate (NEW v3, GPT round-2 must-add)

Phase A.6 test required before any backfill:

```python
# tests/gates/test_embedding_determinism.py
def test_nomic_embed_determinism():
    fixed_strings = load("tests/fixtures/embedding_determinism_50_strings.txt")
    runs = []
    for batch_size in (1, 16, 32):
        for run_idx in range(30):
            vectors = ollama_embed_batch(fixed_strings, batch_size=batch_size)
            runs.append({"batch": batch_size, "run": run_idx, "vectors": vectors})
    # Restart Ollama once, repeat
    restart_ollama_via_systemd()
    for batch_size in (1, 16, 32):
        for run_idx in range(30):
            vectors = ollama_embed_batch(fixed_strings, batch_size=batch_size)
            runs.append({"batch": batch_size, "run": run_idx, "vectors": vectors, "post_restart": True})

    metrics = compute_drift(runs)  # max_abs_diff, cosine_drift, rounded_hash_drift
    assert metrics["cosine_drift_p95"] < 1e-6, f"Embeddings not semantically deterministic: {metrics}"
    write_metrics_to_phase_a_report(metrics)
```

**Manifest checksum policy (NEW v3):**

```yaml
# Per cell version_manifest.json — v3 amended
checksum_sha256:
  source: <sha256(sorted(canonical_solver_id, view_type, text_hash, embedding_model, embedding_dim))>
  vector_diagnostic: <sha256(rounded_vectors_6_decimals)>
  policy: |
    `source` is authoritative for version equality.
    `vector_diagnostic` is observability-only — drift up to cosine 1e-6 is allowed.
```

Raw vector bytes are NOT used for manifest equality because embedding may be deterministic to ε but not bitwise.

### 1.13 Append-only delta ledger (NEW v3, GPT round-2 must-add)

Dream Mode and axiom backfill never write FAISS staging directly. Both append to a per-cell ledger; a single manifest builder consumes:

```
data/faiss_delta_ledger/
  thermal/
    000001_axiom_backfill_2026-04-23T16:00:00Z.jsonl
    000002_dream_mode_2026-04-23T17:30:00Z.jsonl
    000003_manual_patch_2026-04-23T18:00:00Z.jsonl
  energy/
    ...
```

Each ledger entry:

```yaml
seq: 1842                       # monotonic per cell
cell_id: thermal
source: axiom_backfill | dream_mode | manual_patch
canonical_solver_id: heating_cost
canonical_hash: <sha256>
view_type: canonical_fi
text: "Mökin lämmityskustannus..."
embedding_model: nomic-embed-text:v1.5
parent_manifest_version: 2026-04-23T15:30:00Z-3a4f
created_at: 2026-04-23T17:30:00Z
```

**Manifest builder protocol:**

```text
1. Freeze ledger high-water mark H per cell
2. Build staging index = base axioms + all deltas with seq ≤ H
3. Validate staging (oracle, smoke)
4. If gates pass: promote staging to live; record H in manifest
5. Deltas with seq > H remain pending for next manifest
```

**Conflict resolution (deterministic, not first-writer-wins):**

```text
same canonical_hash               → duplicate, no-op
same canonical_solver_id, new hash → new revision, requires validation gate
manual_patch > axiom_backfill > dream_mode  (only for adjudication; never silent)
seq collision                     → allocator bug, abort manifest build
```

### 1.14 Per-query coherent snapshot (NEW v3, GPT round-2 must-add)

RCU protects individual cell reads but a multi-cell query could see `thermal@v3 + energy@v4` if a swap happens mid-query. Fix:

```python
def hybrid_retrieve(query):
    # Take snapshot ONCE at query entry
    snapshot = faiss_registry.current_snapshot()  # lock-free, returns immutable view
    trace.faiss_manifest_version = snapshot.manifest_version

    cells_to_search = decide_candidate_cells(query)
    results = []
    for cell_id in cells_to_search:
        # ALWAYS use snapshot, never fresh registry lookup mid-query
        cell_index = snapshot.cells[cell_id]
        results.extend(cell_index.search(query_vec, k=5))
    return rank_and_dedup(results)
```

Snapshot is refcounted; old snapshots stay alive until in-flight queries finish. New queries use new snapshot. This is standard RCU semantics.

### 1.15 Embedding cache for repeated queries (NEW v3, GPT round-2 add-before-Phase-C)

400h gauntlet repeats queries; caching saves 90%+ embedding cost. Cache embeddings, NOT routing decisions:

```yaml
# configs/settings.yaml addition
embedding_cache:
  enabled: true
  backend: sqlite           # sqlite | disk_jsonl | in_memory_lru
  path: data/embedding_cache.sqlite
  max_entries: 100_000
  key_fields:
    - embedding_model
    - normalized_query_hash    # sha256(lower(strip(query)))
  ttl_days: 90               # invalidate after 90 days OR on embedding_model change
```

Cache key includes `embedding_model` so a model change invalidates automatically. Routing decisions are NOT cached (would be invalidated by every manifest swap).

MAGMA trace fields (NEW v3): `query_embedding_cache_hit`, `query_embedding_hash`.

### 1.16 Disaster recovery extended invariant (NEW v3, GPT round-2 must-add)

Old plan said "rebuild from `configs/axioms/`". GPT round-2 corrected: once Dream Mode contributes, axioms alone are insufficient.

**True invariant:**

```text
FAISS live can be rebuilt from:
  configs/axioms/*.yaml
  + data/faiss_delta_ledger/<cell>/*.jsonl  (up to manifest high-water mark)
  + embedding model + version
  + tools/backfill_axioms_to_hex.py config
```

Phase B.6 disaster recovery dry run:

```python
# tests/gates/test_disaster_recovery.py
def test_rebuild_from_sources_alone():
    # Simulate corruption: copy live to /tmp, delete data/faiss_live/
    backup_live_to_tmp()
    delete_faiss_live()

    # Rebuild from sources
    rebuild_from_axioms_and_ledger(
        axioms_dir="configs/axioms",
        ledger_dir="data/faiss_delta_ledger",
        target_manifest_version=ORIGINAL_VERSION,
    )

    # Compare
    assert canonical_solver_ids_match(restored, original)
    assert source_text_hashes_match(restored, original)
    assert view_counts_match(restored, original)
    assert manifest_checksum_source_match(restored, original)

    # Run oracle smoke (small subset)
    smoke_results = run_oracle_smoke(restored)
    assert smoke_results.pass_rate >= 0.95
```

If disaster recovery fails, Phase D-2 (authoritative) is BLOCKED.

### 1.17 Capability Surface Index split (NEW v3, GPT round-2 add-but-defer)

GPT round-2 noted: multiplicative CSI hides progress when one term zeros (correct safety behavior) but also blinds dashboard to non-safety progress.

**v3 splits into two scores:**

```python
# CSI_strict — multiplicative, safety-gated (any zero collapses)
CSI_strict = (
    cell_occupancy_ratio
    * oracle_recall_at_5
    * verifier_pass_rate
    * (1 - false_solver_activation_rate)
    * (1 - llm_fallback_rate)
    * bounded_composite
    * magma_trace_completeness
)
# Used as: red/green health alarm. Activation gates do NOT use CSI.

# CSI_progress — weighted geometric, components individually bounded
CSI_progress = exp(
    0.20 * log(cell_occupancy_ratio)
  + 0.20 * log(oracle_recall_at_5)
  + 0.15 * log(verifier_pass_rate)
  + 0.15 * log(1 - false_solver_activation_rate)
  + 0.15 * log(1 - llm_fallback_rate)
  + 0.10 * log(bounded_composite)
  + 0.05 * log(magma_trace_completeness)
)
# Used for: trend tracking, executive dashboard, scaling-roadmap progress.

bounded_composite = min(1.0, log1p(useful_composite_paths_total) / log1p(target_useful_composite_paths))
```

**Activation gates (Phase D-1, D-2) rely on individual metrics, NEVER on CSI.** CSI is a high-level summary, not a gate.

### 1.18 Phase C execution scope split (NEW v3, GPT round-2 must-add)

Phase C full run (1000 queries × 3 architectures) on workstation, not edge:

```yaml
phase_c:
  full_run:
    location: workstation       # i7-12850HX, 136 GB RAM
    queries: 1000
    architectures: [keyword, flat, hex]
    embedding_calls_estimate: 5000  # accept higher cost; this is validation, not runtime
    duration_estimate_minutes: 30   # workstation throughput

  edge_smoke:
    location: raspberry_pi_iot  # or any --preset=raspberry-pi-iot deploy
    queries: 50-100
    architectures: [keyword, hex]   # skip flat (not the candidate)
    embedding_hot_path: false (mandatory)
    asserts:
      - keyword_fast_path_rate >= 0.80
      - p95_route_latency_ms <= 30   # edge SLA
      - no_embedding_call_when_keyword_confident
```

Edge smoke proves the lazy hot path actually works on edge hardware. Full validation runs centrally.

---

## 2. Phases (v3, with new gates marked NEW v3)

### Phase A — Pre-flight (1 h, was 30 min)

| Step | Action | Pass criterion |
|---|---|---|
| A.1 | `pip list \| grep faiss-cpu` | `faiss-cpu==1.13.2` |
| A.2 | Test new `/api/embed` endpoint with batch | 200 OK with vector array |
| A.3 | Embedding latency benchmark (batch=1/16/32, p50/p95) | Documented baseline |
| A.4 | If embed p95 > 50 ms → `embedding_hot_path: false` mandatory | Decision recorded |
| A.5 | Backup configs + data/faiss/ | Files in `backup/2026-04-23/` |
| **A.6 NEW v3** | **Embedding determinism test (`tests/gates/test_embedding_determinism.py`)** | **`cosine_drift_p95 < 1e-6`** |

### Phase B — Tools (8 h, was 5 h with v3 additions)

#### B.0 Multi-view axiom backfill with declared cell_id

NEW v3 prerequisites:
- Every axiom YAML must declare `cell_id` and may declare `affinity_cells`
- Every axiom YAML must include `solver_output_schema`
- Backfill aborts on any axiom missing these fields

`tools/backfill_axioms_to_hex.py` writes to **delta ledger** (NEW), never directly to staging FAISS index. Manifest builder consumes ledger.

Tests required (10, was 10):
1. Idempotency — rerun produces identical ledger entries
2. Cell assignment uses DECLARED cell_id (not keyword heuristic)
3. Embedding error → graceful fail, partial ledger entries marked invalid
4. `--filter-domain` works
5. `--dry-run` doesn't write
6. Hash dedup against `solver_hash.canonical_hash`
7. Multi-view: 5 docs per axiom indexed
8. **NEW v3:** `test_multi_view_dedup_top_k` — single solver's 5 views can't fill all top-5 slots
9. Staging never overwrites live
10. Atomic `os.replace` on Windows (mocked)

#### B.1 Cell centroid computation (unchanged from v2)

#### B.2 Three-way shadow runner (unchanged from v2)

#### B.3 Hex manifest tool (versioning + rollback) — extended v3

NEW v3 capability: `tools/hex_manifest.py build` consumes delta ledger:

```bash
# Build new manifest from ledger up to high-water mark
python tools/hex_manifest.py build --ledger-hwm 1842 --output-staging

# Validate staging
python tools/hex_manifest.py validate --staging

# Promote to live (atomic)
python tools/hex_manifest.py commit --to-version 2026-04-23T18:00:00Z-coherent-set-B

# Rollback
python tools/hex_manifest.py rollback --to-version <prev_version>

# Status (drift detection)
python tools/hex_manifest.py status
```

#### B.4 Embedding migration scaffold (unchanged, placeholder)

#### B.5 Question-frame parser + tests (unchanged from v2)

#### **B.6 NEW v3 — Disaster recovery dry run**

`tests/gates/test_disaster_recovery.py` per §1.16. Must pass before Phase D-2.

#### **B.7 NEW v3 — Embedding cache implementation**

`waggledance/core/learning/embedding_cache.py`:

```python
class EmbeddingCache:
    def __init__(self, backend: str, path: str, max_entries: int, ttl_days: int):
        ...
    def get(self, embedding_model: str, query: str) -> Optional[np.ndarray]:
        key = self._key(embedding_model, query)
        if entry := self._backend.get(key):
            if not entry.is_expired(self.ttl_days):
                return entry.vector
        return None
    def put(self, embedding_model: str, query: str, vector: np.ndarray):
        ...
    def _key(self, embedding_model: str, query: str) -> str:
        normalized = query.strip().lower()
        return hashlib.sha256(f"{embedding_model}|{normalized}".encode()).hexdigest()
```

Tests (5):
1. Hit / miss
2. TTL expiry
3. Model-change invalidates cache
4. Concurrent get/put thread safety (SQLite with WAL)
5. Cache corruption → graceful fall-through to embed call (do not crash)

### Phase C — Oracle-first shadow validation (8 h workstation, +30 min edge smoke)

Updated execution scope per §1.18.

Decision gates after Phase C unchanged from v2.

### Phase D-1 — Candidate mode (24 h, was 24 h)

NEW v3 prerequisite: All axioms must have `solver_output_schema` (so postprocessor works for boolean-threshold queries).

### Phase D-2 — Authoritative mode (10 min)

NEW v3 prerequisites:
- Disaster recovery test (B.6) passed
- Embedding determinism test (A.6) passed
- Embedding cache deployed
- All axioms have `cell_id` + `solver_output_schema`

### Phase E — 24 h hardened measurement (Grok kill signals, unchanged)

### Phase F — Document + propagate (1 h)

Plus NEW v3:
- Update `docs/HYBRID_RETRIEVAL.md` with measured numbers
- Defer Prometheus/dashboard expansion to follow-up plan (per GPT round-2: do not block Phase A)

---

## 3. MAGMA retrieval trace schema (v3, +3 fields)

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
  query_embedding_cache_hit: bool          # NEW v3
  query_embedding_hash: <sha256>           # NEW v3
  retrieval_snapshot_manifest_version: <id>  # NEW v3
  keyword_cell: <cell_id>
  keyword_confidence: <float>
  centroid_cells_top3:
    - {cell: thermal, score: 0.87}
    - {cell: energy, score: 0.71}
    - {cell: safety, score: 0.42}
  searched_cells:
    - {cell: thermal, ring_depth: 0, non_empty: true, faiss_version: 2026-04-23T15:30:00Z-3a4f}
  candidate_doc_ids:
    - {doc_id: heating_cost#canonical_fi, score: 0.91, view_type: canonical_fi}
  chosen_canonical_solver_id: heating_cost
  chosen_score: 0.91
  fallback_stage: local_cell_faiss | ring1_faiss | ring2_faiss | global_chroma | llm_fallback | no_answer
  faiss_manifest_version: 2026-04-23T16:00:00Z-coherent-set-A
  empty_cell_miss: false
  keyword_centroid_disagreement: false
  question_frame:
    desired_output: numeric | boolean_comparison | explanation | diagnosis | optimization
    comparator: {op: ">", threshold: 50, unit: EUR}
    negation: {present: false, scope: null}
  solver_output_schema_used: true          # NEW v3
  postprocessor_applied: threshold_compare # NEW v3
  verifier_result: pass | fail | not_run
  quality_tier: GOLD | SILVER | BRONZE | QUARANTINE | null
  total_routing_latency_ms: <float>
```

---

## 4. Risks (v3 reassessment)

| # | Risk | Status v3 | Mitigation v3 |
|---|---|---|---|
| 1 | Cell mis-assignment query-side | Mitigated | §1.2 keyword + centroid parallel |
| 2 | Cell mis-assignment source-side | **NEW v3 mitigation** | §1.2 declared cell_id + audit |
| 3 | No-GPU edge breaks <1ms | Mitigated | §1.4 lazy hot path |
| 4 | Hex topology adds complexity for no gain | Mitigated | §1.5 measured vs flat |
| 5 | FAISS read-during-write race | Mitigated | §1.9 + §1.13 + §1.14 |
| 6 | Multilingual asymmetry FI/EN | Mitigated | §1.7 multi-view |
| 7 | Negation/question-type misrouting | Mitigated | §1.8 + §1.18 schema |
| 8 | Cell version drift (8 cells, 8 versions) | Mitigated | §1.10 manifest |
| 9 | Embedding model deprecation | Acceptable | §1.11 scaffold |
| 10 | Keyword-truth bias in shadow validation | Mitigated | §1.6 three sources |
| 11 | Memory growth from indices | Acceptable (Gemini: <120 MB at 40k) | Watchdog covers |
| 12 | **Embedding non-determinism corrupts manifest** | **NEW v3 mitigation** | §1.12 test + source-based checksum |
| 13 | **Dream Mode + backfill staging race** | **NEW v3 mitigation** | §1.13 append-only ledger |
| 14 | **In-flight query manifest swap inconsistency** | **NEW v3 mitigation** | §1.14 per-query snapshot |
| 15 | **Disaster recovery insufficient** (axioms alone) | **NEW v3 mitigation** | §1.16 axioms + ledger |
| 16 | **Embedding cost on 400h gauntlet** | **NEW v3 mitigation** | §1.15 cache |
| 17 | **CSI multiplicative collapse hides progress** | **NEW v3 mitigation** | §1.17 split strict / progress |

17 risks total (10 in v2, 7 added in v3 from GPT round-2).

---

## 5. Implementation order (v3, +3 h)

```
Phase A: Pre-flight (incl. A.6 determinism test)              1 h
Phase B.0: Multi-view backfill + declared cell_id requirement 3 h
Phase B.1: Cell centroid computation                          1 h
Phase B.2: Three-way shadow runner                            2 h
Phase B.3: Hex manifest tool (build/commit/rollback/status)   2 h  (was 1 h)
Phase B.4: Embedding migration scaffold                       30 min
Phase B.5: Question-frame parser + solver_output_schema       3 h  (was 2 h)
Phase B.6: NEW — Disaster recovery dry run                    1 h
Phase B.7: NEW — Embedding cache implementation               2 h
Phase C: Oracle validation + 3-way shadow (workstation)       8 h (overnight)
Phase C.edge: NEW — Edge smoke on RPi (50-100 queries)        30 min
        ↓ DECISION GATE: hex / flat / abandon
Phase D-1: Candidate mode                                     24 h (live)
        ↓ DECISION GATE: hybrid_unique_correct ratio
Phase D-2: Authoritative mode                                 10 min
Phase E: 24 h hardened measurement (kill signals)             24 h (live)
Phase F: Document + propagate                                 1 h
                                                              ─────
Total active work:                                            ~16 h (was 10 h)
Total elapsed:                                                ~3 days (unchanged)
```

The added hours go entirely into the protocol-hardening GPT round-2 demanded. Elapsed time unchanged because most additions parallelize with existing work.

---

## 6. Decision authority matrix (v3, unchanged from v2)

| Decision | Who decides | Evidence required |
|---|---|---|
| Phase A → B | Auto if A pass (incl. A.6) | A pre-flight report + determinism evidence |
| Phase B → C | Auto if all B tests pass (incl. B.6, B.7) | B test results |
| Phase C → D-1 vs abandon-hex vs don't-enable | **Jani** | Three-way shadow report + §1.5 gate matrix |
| Phase D-1 → D-2 | Auto if 3:1 ratio + 0 regressions + B.6 pass | Phase D-1 report after 24 h |
| Rollback during Phase E | Watchdog automatic | Any kill signal hit |
| Re-attempt after rollback | **Jani** | Incident root-cause document |

---

## 7. Out of scope (v3, expanded)

Not in this plan, deferred:

1. Cross-encoder reranker (Gemini suggestion). Future scaling work.
2. Replacing hex with flat-only (Grok suggestion). Conditional follow-up if Phase C says so.
3. Wait until 200 solvers (Grok suggestion). Tradeoff accepted; shadow now is no-risk.
4. MicroModel V3 LoRA classifier as primary router. Memory plan Week 5+.
5. Subdivision of cells (Level 1+). Premature.
6. **Prometheus/dashboard expansion** (NEW v3 deferral, GPT round-2 explicit). Follow-up after Phase C decision.
7. **Full CSI dashboard** (NEW v3 deferral). Just publish CSI_strict and CSI_progress as numbers; no UI work in this plan.

---

## 8. Success criteria (v3, expanded)

After full execution Phase A-F:

1. ✅ Three independent ground truth sources exist and checked into git
2. ✅ Phase C three-way shadow report with measured numbers
3. ✅ Decision documented: hex / flat / don't-enable, linked to data
4. ✅ If hex enabled: 24 h authoritative mode passed with 0 kill triggers
5. ✅ MAGMA retrieval traces include all §3 fields for 100 % of routed queries
6. ✅ FAISS version manifest exists and rollback tested (B.3 status + B.6 disaster recovery)
7. ✅ No regression: 400 h campaign green hours rate, XSS/DOM/auth, memory growth
8. ✅ `docs/HYBRID_RETRIEVAL.md` updated with measured numbers
9. ✅ `memory/project_hex_solver_scaling.md` precondition `hybrid_retrieval.loaded: true, mode: authoritative` marked done
10. **NEW v3:** Embedding determinism evidence in Phase A report
11. **NEW v3:** Disaster recovery dry-run pass logged (B.6)
12. **NEW v3:** Embedding cache hit rate ≥ 50 % during 400 h gauntlet
13. **NEW v3:** Edge smoke (RPi) confirms `embedding_hot_path=false` actually works

---

## 9. Where credit goes (v3 update)

Existing v2 attributions retained. NEW v3 attributions:

- **Source-side axiom cell honesty + declared cell_id requirement** — GPT round-2 (caught a v2 hidden failure mode)
- **Embedding determinism gate + source-based manifest checksum** — GPT round-2
- **Append-only delta ledger for Dream Mode + backfill** — GPT round-2
- **Per-query coherent snapshot rule** — GPT round-2
- **Embedding cache with model-keyed invalidation** — GPT round-2
- **Disaster recovery extended invariant (axioms + ledger + model)** — GPT round-2
- **CSI split into strict + progress** — GPT round-2
- **Solver output schema requirement** — GPT round-2
- **Phase C scope split (workstation full vs edge smoke)** — GPT round-2
- **`enabled` → `loaded` rename** — GPT round-2
- **GOLD/SILVER trajectory router/verifier version filter** — GPT round-2
- **Multi-view dedup test** — GPT round-2

GPT round-2 contributed 12 distinct amendments. v3 = v2 + GPT round-2.

---

## 10. Final pre-implementation question for Jani (v3)

This v3 plan is **implementation-ready** per GPT round-2's verdict ("Approve with minor amendments — Phase A may begin if Phase A is expanded to include these checks"). All amendments are now folded in.

Two paths:

- **A. Send v3 to GPT for one final pass** — accountability check that all 12 round-2 amendments landed correctly. ~30 min user time, low risk of finding new issues, near-zero risk of finding showstoppers.
- **B. Begin Phase A directly** — trust that synthesis was faithful. Phase A includes the new A.6 determinism test which itself is a quality gate.

I (Claude) recommend **A** — same logic as before: 30 min sanity check beats 10 h debug. But path B is also defensible given the rigor of round-2.

Awaiting your decision. If A, run `tools/cell_manifest.py` (already exists) and prepare a round-3 prompt. If B, begin with Phase A.1 immediately.
