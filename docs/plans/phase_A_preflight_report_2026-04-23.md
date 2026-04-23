# Phase A — Pre-flight Report

**Date:** 2026-04-23
**Plan reference:** `hybrid_retrieval_activation_v3_2026-04-23.md` + v3.1 amendments
**Hardware:** i7-12850HX, 136 GB RAM, Windows 11, Python 3.13, Ollama 0.21.0
**Verdict:** ✅ **Phase A COMPLETE — proceed to Phase B**

## Results per step

### A.1 — faiss-cpu installed ✅

```
faiss-cpu  1.13.2
numpy      2.2.6
httpx      0.28.1
```

### A.2 — Ollama /api/embed works ✅

New endpoint `POST /api/embed` with batch `input` array returns 768-dim vectors. Old `/api/embeddings` not used.

### A.3 — Embedding latency benchmark

| batch | p50 total | p95 total | p50 per-item | p95 per-item |
|---:|---:|---:|---:|---:|
| 1  | 3166 ms | 3225 ms | 3166 ms | 3225 ms |
| 16 | 3358 ms | 3602 ms | 210 ms | 225 ms |
| 32 | 3618 ms | 3657 ms | 113 ms | 114 ms |

**Dominant cost:** ~3 s fixed per HTTP call. Batch amortizes this. At batch=32, per-item latency is 113 ms.

### A.4 — Decision: `embedding_hot_path: false` MANDATORY

Single-embed p95 = 3225 ms >> 50 ms threshold.

**Implication:** keyword routing stays the hot path. Embedding runs only on miss / low-confidence (per v3 §1.4 lazy embedding hot path). Grok's concern validated on this hardware.

**Backfill batch size recommendation:** 32 (best per-item latency).

### A.5 — Backups saved ✅

```
backup/2026-04-23/settings.yaml.pre-hybrid       (config pre-activation)
backup/2026-04-23/faiss_pre-hybrid.tar.gz        (32 MB — existing indices)
```

### A.6 — Embedding determinism gate ✅

| Metric | Value | Threshold | Pass |
|---|---:|---:|:---:|
| same-process cosine drift p95 | 1.00e-12 | 1e-6 | ✅ |
| post-restart cosine drift p95 | — | — | skipped_with_reason (Ollama user-process, not Windows service; v3.1 tweak 6 allows) |
| rounded-hash drift detected | false | false | ✅ |

**Implication for manifest checksum policy (v3 §1.12):** vectors are semantically deterministic to machine precision. We could safely use either:
- **Source-based manifest checksum** (v3 canonical: `sha256(sorted(canonical_solver_id, view_type, text_hash, embedding_model, embedding_dim))`), OR
- **Raw-vector rounded-to-6-decimals checksum** (v3 diagnostic).

Stick with source-based per v3 for architectural robustness. Raw vector diagnostics stored separately.

**Full results:** `docs/plans/phase_A6_determinism_results.json`

## Gate decisions applied

Per v3 + v3.1:

- `hybrid_retrieval.embedding_hot_path: false` — mandatory
- Backfill `batch_size: 32`
- Manifest checksum policy: source-based (raw-vector as diagnostic only)
- Determinism gate: PASS (same-process; post-restart skipped per v3.1 tweak 6)

## Next steps — Phase B

Per v3 §2, Phase B has 8 tools to build (B.0 through B.7). Critical prerequisite from v3.1 tweak 2: **resolve the ledger/base-axiom double-count ambiguity before B.0 starts**. Decision made in v3.1: **ledger-only pattern** (`configs/axioms/*.yaml` is read-only source; backfill emits ledger entries; manifest builder consumes ledger entries only).

Phase B order:

1. **B.0** — Multi-view axiom backfill tool (**3 h**, requires declared `cell_id` + `solver_output_schema` in every axiom)
2. **B.1** — Cell centroid computation (**1 h**)
3. **B.2** — Three-way shadow runner (keyword / flat / hex) (**2 h**)
4. **B.3** — Hex manifest tool (build / commit / rollback / status) (**2 h**, includes snapshot context manager per v3.1 tweak 7)
5. **B.4** — Embedding migration scaffold (**30 min**)
6. **B.5** — Question-frame parser + solver_output_schema (**3 h**)
7. **B.6** — Disaster recovery dry run (**1 h**)
8. **B.7** — Embedding cache with NFC normalization (**2 h**, v3.1 tweak 3)

Total Phase B: ~14 h active work.

Recommended split: B.0 + B.1 + B.3 in one session (core tool group), then B.5 + B.8 in next (parsers + cache), then B.2 + B.6 in final session (validation tools) before Phase C overnight.

## Readiness check complete

Phase B.0 is cleared to begin. Awaiting user confirmation.
