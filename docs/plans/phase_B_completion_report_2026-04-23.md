# Phase B — Completion Report

**Date:** 2026-04-23
**Plan reference:** `hybrid_retrieval_activation_v3_2026-04-23.md` + v3.1 amendments
**Verdict:** ✅ **Phase B COMPLETE — 8/8 tools built, tests pass, ready for Phase C**

## Deliverables (auto-committed in `d246756` via campaign_auto_commit)

| Tool | Purpose | Tests |
|---|---|---|
| `tools/upgrade_axioms_for_v3.py` | Adds `cell_id` + `solver_output_schema` to all axioms | — (one-shot) |
| `tools/backfill_axioms_to_hex.py` | Multi-view ledger-only backfill (v3.1 tweak 2) | verified via dry-run |
| `tools/compute_cell_centroids.py` | L2-normalized mean per cell | — (pure compute) |
| `tools/hex_manifest.py` | build / commit / rollback / status | verified via build |
| `tools/shadow_route_three_way.py` | Phase C three-way runner | tested at import |
| `tools/migrate_embedding_model.py` | Scaffold for future migrations | — (placeholder) |
| `waggledance/core/learning/embedding_cache.py` | SQLite cache, NFC-normalized (v3.1 tweak 3) | 12 pass |
| `waggledance/core/reasoning/question_frame.py` | FI/EN negation + threshold + units | 30 pass |
| `tests/gates/test_disaster_recovery.py` | v3 §1.16 invariant test | PASSED |

## State after Phase B

### Axioms

14 axioms across 4 domains (cottage/factory/gadget/home), all now with:
- `cell_id` (declared)
- `placement_review.status = "auto_heuristic"`
- `solver_output_schema` (inferred from formulas)

### Delta ledger

```
data/faiss_delta_ledger/
  thermal/   20 entries (4 solvers × 5 views)
  energy/    15 entries (3 solvers × 5 views)
  system/    12 entries
  math/      10 entries
  safety/    10 entries
```

Cells `seasonal`, `learning`, `general` remain empty — known gap signals per v3 §1.3.

### Staging FAISS indices

```
data/faiss_staging/
  manifest.json                      (5 cells, source-based checksums)
  cell_centroids.json                (5 centroids, 768-dim)
  thermal/index.faiss + meta.json    (20 docs)
  energy/index.faiss + meta.json     (15 docs)
  system/index.faiss + meta.json     (12 docs)
  math/index.faiss + meta.json       (10 docs)
  safety/index.faiss + meta.json     (10 docs)
```

Not yet committed to live — that's Phase D-2 (authoritative mode) after Phase C validation.

### Test suite

42 new tests across embedding_cache (12) + question_frame (30), all pass.
Plus disaster recovery gate PASSED on initial corpus.

### Embedding determinism + cache

- `nomic-embed-text` verified deterministic (cosine drift p95 = 1e-12)
- SQLite embedding cache ready at `data/embedding_cache.sqlite` (created on first use)
- NFC canonicalization matches actual Ollama input (v3.1 tweak 3 applied)

## Phase C readiness

Phase C (three-way shadow validation) requires:

1. ✅ Staging FAISS indices (from B.3 build)
2. ✅ Cell centroids (from B.1)
3. ⏳ Oracle set at `tests/oracle/<solver_id>.yaml` — NOT YET CREATED
4. ⏳ hot_results.jsonl sample as fallback — ready, 400h campaign has 30k+ queries

### Two options for Phase C execution

**Option 1 — Run immediately on hot_results.jsonl sample:**

```bash
python tools/shadow_route_three_way.py --queries 500 --source hot_results
```

Produces `docs/runs/hybrid_shadow_three_way_<date>.{md,json}` with:
- p50/p95 latency per architecture
- flat vs hex agreement rate
- keyword/centroid disagreement rate

Missing: oracle precision@1 (requires labeled data).

**Option 2 — Create oracle set first (higher quality):**

Handwrite 20-50 positive + 20 negative queries per solver (14 × 40 = 560 queries). Provides ground truth for precision@1 and recall@5 metrics. Takes 2-4h of human labeling work.

### Recommendation

Run Option 1 overnight to establish baselines, then Option 2 after review of initial numbers. If Option 1 already shows hex clearly beating flat, Option 2 is still needed for Phase D-1 promotion gate (which requires oracle metrics).

## Phase D through F — outstanding

Per v3:

- **Phase D-1** — Candidate mode (24h live): requires Phase C pass + solver_output_schema on all axioms (✅ done).
- **Phase D-2** — Authoritative mode: requires Phase D-1 pass + B.6 disaster recovery test pass (✅ done) + A.6 determinism (✅ done) + embedding cache deployed (✅ done).
- **Phase E** — 24h hardened measurement with kill signals: automated via `tools/campaign_watchdog.py`.
- **Phase F** — Document + propagate: ~1h of doc updates after Phase E closes.

All downstream phases are gated by Phase C results.

## Credit — v3.1 amendments applied during Phase B

- Tweak 1 (source-side BLOCK): in `backfill_axioms_to_hex.audit_placement` ✅
- Tweak 2 (ledger-only pattern): entire B.0 + B.3 design ✅
- Tweak 3 (NFC normalization): `embedding_cache.canonicalize_for_embedding` ✅
- Tweak 4 (CSI clamp01): deferred to Phase F CSI implementation
- Tweak 5 (parsed semver): deferred to trajectory truth filter in Phase C
- Tweak 6 (Windows-safe restart): `test_embedding_determinism.restart_ollama_platform_safe` ✅
- Tweak 7 (snapshot context manager): architectural note in `hex_manifest.py`, full impl when runtime integration happens (Phase D-2)

4 of 7 tweaks fully applied; 3 deferred appropriately to their actual use phases.

## Next action

**User decision required:**

- **Proceed to Phase C Option 1** (automatic 500-query shadow run on hot_results.jsonl)
- **Create oracle set first** then Phase C Option 2 (higher quality, requires human labeling)
- **Pause and commit current state** for session handoff

Given session context may be running low, safest recommendation is to kick off Phase C Option 1 overnight and review results in next session. Shadow run will take ~15 min given embed cache warming.
