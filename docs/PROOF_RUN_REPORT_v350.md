# Final Pre-Merge Report — v3.5.0

**Status:** READY FOR MERGE
**Date:** 2026-04-04
**Run ID:** 20260402_084233
**Branch:** main (merged via PR #44)
**PR:** #44
**Tag:** v3.5.0
**Fix cycles used:** 1 of 3

## Executive Summary

Full autonomous proof run of v3.5.0 features: hybrid retrieval with hex-cell FAISS, solver candidate lab, and synthetic training accelerator. All phases P7A–P7H passed. 30h soak completed: 750/750 cycles, 4500/4500 requests, 0 failures, 0 5xx.

## Phase Status

| Phase | Description | Status | Detail |
|-------|-------------|--------|--------|
| P7A | Baseline benchmark (hybrid OFF) | PASS | 16 queries, p50=9055ms |
| P7B | Hybrid benchmark before backfill | PASS | Cells empty, fell through to global |
| P7C | Explicit backfill | PASS | 4890/5000 indexed, 8 cells populated |
| P7D | Hybrid benchmark after backfill | PASS | p50=4231ms (-53%), LLM fallback 0% |
| P7E | Candidate lab proof | PASS | 2 compiled, 0 proposed, 5/5 AST rejections |
| P7F | Accelerator proof | PASS | 200->568 rows, perfect class balance |
| P7G | 30h proof soak | PASS | 750/750 cycles, 4500 req, 0 fail, 0 5xx |
| P7H | PR update + verdict | PASS | Report finalized, verdict READY FOR MERGE |

## Commits on Feature Branch

| Hash | Message |
|------|---------|
| 3de15a3 | feat(hybrid): add idempotent cell backfill and population metrics |
| 2ab62f2 | feat(lab): add safe solver candidate lab and template compiler |
| 1b67b55 | feat(learning): add synthetic accelerator and optional GPU specialist training |
| 8c22a55 | feat(api): add hybrid backfill and candidate-lab observability endpoints |
| 699d004 | docs(v3.5.0): truth-audit docs and add demo materials |
| 490425e | fix(verify): harden validation issues found during v3.5.0 live proof |

## Test Summary

| Suite | Count | Result |
|-------|-------|--------|
| Hybrid backfill | 12 | PASS |
| Solver candidate lab | 42 | PASS |
| Synthetic accelerator | 20 | PASS |
| Candidate lab routes | 11 | PASS |
| **New tests total** | **85** | **PASS** |
| **Full pytest** | **4898** | **4898 pass, 3 skip, 0 fail** |

## Benchmark Results

| Metric | Baseline (OFF) | Before Backfill | After Backfill | Change |
|--------|---------------|-----------------|----------------|--------|
| p50 Latency | 9055 ms | 8893 ms | 4231 ms | -53% |
| p95 Latency | 12758 ms | 13418 ms | 4345 ms | -66% |
| LLM Fallback | 75% | 75% | 0% | -75pp |
| Local FAISS | 0% | 0% | 75% | +75pp |

## Hybrid Backfill

| Metric | Value |
|--------|-------|
| Cases scanned | 5000 |
| Indexed | 4890 |
| Skipped (no content) | 110 |
| Failed | 0 |
| Cells populated | 8/8 |
| Idempotency | Verified (2nd run: 0 new) |

Cell distribution: general=3747, thermal=455, math=182, learning=153, system=125, energy=119, seasonal=97, safety=12.

## Candidate Lab (P7E)

| Metric | Value |
|--------|-------|
| Cases analyzed | 499 |
| Candidates generated | 2 |
| Compiled | 2 |
| Proposed (loaded to routing) | 0 |
| Failed validation | 0 |
| AST rejections tested | 5/5 correct |

Candidates: `cand_observe_5df49489ea71` (confidence 0.80), `cand_protect_c3e13eab271b` (confidence 0.60). Both compiled as safe deterministic stubs. No candidates entered production routing.

AST validator correctly rejected: `import os`, `exec()`, `open()`, `__import__()`, `eval()`.

## Synthetic Accelerator (P7F)

| Metric | CPU Path | GPU-enabled |
|--------|----------|-------------|
| Device | cpu | cpu (cuML not installed) |
| Real rows | 200 | 200 |
| Synthetic rows | 368 | 368 |
| Total rows | 568 | 568 |
| Train time | 6.7 ms | 7.1 ms |

Class balance before: BRONZE=142, SILVER=38, GOLD=15, QUARANTINE=5.
Class balance after: All classes = 142 (perfect balance).

All synthetic rows tagged `_synthetic=True` with `_source_hash` provenance. GPU available (RTX A2000 8GB) but cuML/RAPIDS not installed — graceful CPU fallback by design.

## 30h Proof Soak (P7G)

| Metric | Value |
|--------|-------|
| Target | 750 cycles, 30h |
| Endpoints per cycle | 6 |
| Total target requests | 4500 |
| Cycles completed | 750 |
| Requests completed | 4500 |
| Failures | 0 |
| 5xx errors | 0 |
| Duration | 107 869s (30.0h) |
| Verdict | PASS |

Endpoints monitored: `/api/status`, `/api/ops`, `/api/hybrid/status`, `/api/hybrid/backfill/status`, `/api/candidate_lab/status`, `/api/learning/accelerator`.

## Risk Assessment

| Risk | Mitigation | Level |
|------|-----------|-------|
| FAISS cell corruption | Backfill is additive only, no destructive reindex | LOW |
| Candidate lab escapes to prod | No router wiring, explicit isolation, proposal-only | LOW |
| GPU dependency breaks CPU path | GPU optional, off by default, clean fallback | LOW |
| API breaking changes | All additive, old fields preserved, tested | LOW |

## Feature Flags & Defaults

| Feature | Flag | Default |
|---------|------|---------|
| Hybrid retrieval | `hybrid_retrieval.enabled` | **OFF** |
| Candidate lab | Always available (read-only) | Proposal-only |
| Synthetic accelerator | `learning.gpu_enabled` | **OFF** (CPU) |
| Backfill | Manual trigger only | NOT auto-run |

## Fix Cycles

1/3 used. Single fix in commit `490425e`:
- `_extract_content()` in backfill service rewritten to handle CaseTrajectory dict format from SQLiteCaseStore
- Added `_extract_intent()` and `_extract_query()` helpers
- Regression tests added

## Verdict

**READY FOR MERGE**

All v3.5.0 proof criteria met:
- P7A-P7D: Benchmarks show -53% p50 latency, LLM fallback 75% -> 0% after backfill
- P7E: Candidate lab produced 2 safe compiled candidates, 0 routed to production, AST validator 5/5 rejections correct
- P7F: Synthetic accelerator balanced 4 classes perfectly (200 -> 568 rows), GPU fallback clean
- P7G: 30h soak completed with 4500/4500 requests OK, 0 failures, 0 5xx, 0 harmful restarts
- All features behind feature flags (OFF by default)
- 4898 pytest tests pass, 0 failures
- 1/3 fix cycles used (backfill content extraction)
