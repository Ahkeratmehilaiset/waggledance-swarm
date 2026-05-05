# Phase 8.5 Gap Miner — Read-Only Source Inventory

**Date (UTC):** 2026-05-05
**Performed in:** `phase18b/gap-miner-feedback` worktree (read-only access to `origin/phase8.5/curiosity-organ`).

This inventory was produced by `git ls-tree origin/phase8.5/curiosity-organ` and `git show origin/phase8.5/curiosity-organ:<path>`. **No file on `phase8.5/*` branches was mutated.** No fetch beyond the standard `git fetch origin --tags --prune` was performed.

## 1. Branches surveyed

```
$ git branch -r | grep 'phase8.5'
  origin/phase8.5/curiosity-organ
  origin/phase8.5/dream-curriculum
  origin/phase8.5/hive-proposes
  origin/phase8.5/self-model-layer
  origin/phase8.5/vector-chaos
```

The gap miner lives on `origin/phase8.5/curiosity-organ`. The other four branches are out of scope for Phase 18B (producer fabric was already ported in Phase 17A; the rest are not Phase 18B's responsibility).

## 2. Recent history on `origin/phase8.5/curiosity-organ`

```
$ git log --oneline origin/phase8.5/curiosity-organ -10
2efc4f7 verify(phase8.5): commit Session A real curiosity outputs from gap_miner run
8cedac1..584d7b0 campaign(400h): auto-checkpoint HOT=175.1h ...
1a31b24 docs(phase8.5): GAP_MINER_VISION + state.json updated for R7.5 spec
59966b0 test(phase8.5): cover R7.5 tightened test categories (16 new cases)
```

Tip = `2efc4f7`. The R7.5-stricter spec landed at `1a31b24`. The 16-test extension landed at `59966b0`. The remaining commits are 400-hour campaign auto-checkpoints (out of scope).

## 3. Files inventoried

```
$ git ls-tree -r origin/phase8.5/curiosity-organ | grep -i "gap_miner\|gap_signal\|runtime_gap\|curiosity"
docs/architecture/GAP_MINER_VISION.md
docs/runs/curiosity/6b766421f410/curiosity_log.jsonl
docs/runs/curiosity/6b766421f410/curiosity_report.md
docs/runs/curiosity/6b766421f410/curiosity_summary.json
docs/runs/curiosity/6b766421f410/teacher_packs/_unattributed.json
docs/runs/curiosity/6b766421f410/teacher_packs/safety.json
docs/runs/curiosity/6b766421f410/teacher_packs/seasonal.json
docs/runs/curiosity/6b766421f410/teacher_packs/system.json
docs/runs/phase8_5_curiosity_session_state.json
tests/autonomy/test_impact_curiosity.py
tests/fixtures/gap_miner_sample/hot_results.jsonl
tests/fixtures/gap_miner_sample/query_corpus.json
tests/test_gap_miner.py
tools/gap_miner.py
```

## 4. `tools/gap_miner.py` shape (read-only sample)

* ~700 LOC.
* Reads campaign artifacts from disk: `hot_results.jsonl`, `incident_log`, `magma hybrid candidate trace`, `hex subdivision plan`, `composition report`, `cell manifests`.
* Pins the artifact set at session start (deterministic content hashes via SHA-256).
* Emits a 4-output deterministic curiosity contract:
  * `<out_dir>/curiosity_summary.json`
  * `<out_dir>/curiosity_report.md`
  * `<out_dir>/curiosity_log.jsonl`
  * `<out_dir>/teacher_packs/<cell>.json`
* Domain vocabulary used: `CELLS = (general, thermal, energy, safety, seasonal, math, system, learning)`. `_CELL_KEYWORDS` per cell. `GAP_TYPES = (missing_solver, improvement_opportunity, bridge_composition, unit_family_mismatch, contradiction_surface, low_confidence_routing, subdivision_pressure, meta_solver_opportunity)`. `NEXT_ACTIONS = (propose_solver, improve_solver, propose_bridge, propose_subdivision, clarify_routing, propose_meta_solver, do_nothing)`.
* Latency thresholds: `LATENCY_FALLBACK_HIGH_MS = 8000`, `LATENCY_FALLBACK_LOW_MS = 1500`.
* Evidence-strength thresholds: `EVIDENCE_HIGH_MIN = 8`, `EVIDENCE_MEDIUM_MIN = 3`.

## 5. Why a verbatim port is the wrong shape for Phase 18B

| Phase 8.5 `tools/gap_miner.py` | Phase 18B requirement |
| --- | --- |
| **Input:** ~6 distinct on-disk artifacts (hot_results, incident_log, magma trace, subdivision plan, composition report, cell manifests). | **Input:** structured runtime gap signals (single record shape). |
| **Output:** 4 cross-referenced reports (summary, MD, JSONL log, per-cell teacher packs). | **Output:** one `GapMiningResult` with per-candidate `GapVerdict` ∈ 6-element enum, and serializable solver specs / quarantined handoffs. |
| **Domain:** cells × gap-types × next-actions. Curiosity report. | **Domain:** six low-risk allowlist families × verdict enum. Feedback loop. |
| **Coupling:** reads cell_manifest vocabulary, hex subdivision plan, magma trace shape. | **Coupling:** stdlib + WaggleDance `autonomy_growth` package only; no campaign-artifact reader. |
| **CLI:** `tools/gap_miner.py --campaign-dir DIR --apply`. | **CLI:** `tools/run_phase18b_gap_miner_feedback_proof.py --out-dir DIR`. |

Verdict: **REIMPLEMENT_SMALLER_MAINLINE.** The Phase 8.5 module's vocabulary (gap-type taxonomy, evidence thresholds, deterministic hashing pattern) informs the Phase 18B design but the modules ship separately. Phase 8.5 stays on its branch as historical research.

## 6. Open questions surfaced

* The Phase 12 `RuntimeGapDetector` writes to `runtime_gap_signals` SQLite table. The Phase 18B harness uses synthetic fixture signals so it does not require a live DB. A future session could wire `RuntimeGapDetector → mine_runtime_gaps()` through a thin reader; that's an explicit non-goal for Phase 18B.
* `tools/gap_miner.py` Phase 8.5 has 42 tests against fixture campaign artifacts — those tests are scoped to the Phase 8.5 module and stay on branch.

## 7. Inventory result

`PRESERVE_ON_BRANCH_ONLY` for the Phase 8.5 module + tests + fixtures + design doc. `REIMPLEMENT_SMALLER_MAINLINE` is the verdict for Phase 18B's mainline implementation. See `phase85_gap_miner_reconciliation_matrix.md` for the per-file table.
