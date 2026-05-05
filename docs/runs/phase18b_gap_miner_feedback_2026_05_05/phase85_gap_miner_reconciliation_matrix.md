# Phase 8.5 Gap Miner — Reconciliation Matrix for Phase 18B

**Date (UTC):** 2026-05-05
**Source branch (read-only):** `origin/phase8.5/curiosity-organ` @ `2efc4f7`
**Target branch:** `phase18b/gap-miner-feedback`

Each Phase 8.5 item is classified as one of:

* `PORT_CORE` — copy verbatim into mainline.
* `PORT_TEST` — copy test verbatim into mainline.
* `REIMPLEMENT_SMALLER_MAINLINE` — write a smaller, narrower mainline equivalent that does not depend on Phase 8.5 artifact shapes.
* `PRESERVE_ON_BRANCH_ONLY` — keep on phase8.5 branch as historical research.
* `OUT_OF_SCOPE` — not relevant to Phase 18B.
* `BLOCKED_MISSING_SOURCE` — would be needed but cannot be located.

## Per-file matrix

| Phase 8.5 path | Verdict | Mainline action |
| --- | --- | --- |
| `tools/gap_miner.py` | **REIMPLEMENT_SMALLER_MAINLINE** | New mainline modules under `waggledance/core/autonomy_growth/`: `gap_mining.py`, `gap_candidate.py`, `gap_training_data.py`. Different input shape (runtime signal records vs campaign artifacts), different output shape (per-candidate verdict vs cross-referenced reports). The Phase 8.5 gap-type taxonomy and evidence-strength thresholds inform the design but are not imported. |
| `docs/architecture/GAP_MINER_VISION.md` | **PRESERVE_ON_BRANCH_ONLY** | Branch keeps the design rationale. Phase 18B writes its own design doc at `docs/runs/phase18b_gap_miner_feedback_2026_05_05/gap_miner_feedback_design.md`. |
| `tests/test_gap_miner.py` | **PRESERVE_ON_BRANCH_ONLY** | 42 tests scoped to the Phase 8.5 module. Phase 18B has its own test suite at `tests/autonomy_growth/test_phase18b_gap_miner_feedback.py` (≥17 tests) covering the smaller mainline contract. |
| `tests/fixtures/gap_miner_sample/hot_results.jsonl` | **PRESERVE_ON_BRANCH_ONLY** | Campaign-artifact fixture; not the Phase 18B input shape. Phase 18B uses an in-process synthetic-signal fixture inside the proof harness. |
| `tests/fixtures/gap_miner_sample/query_corpus.json` | **PRESERVE_ON_BRANCH_ONLY** | Same reasoning. |
| `tests/autonomy/test_impact_curiosity.py` | **OUT_OF_SCOPE** | Tests the curiosity-impact integration; Phase 18B does not introduce a curiosity organ on main. |
| `docs/runs/curiosity/6b766421f410/curiosity_summary.json` | **PRESERVE_ON_BRANCH_ONLY** | Historical campaign output. |
| `docs/runs/curiosity/6b766421f410/curiosity_report.md` | **PRESERVE_ON_BRANCH_ONLY** | Historical campaign output. |
| `docs/runs/curiosity/6b766421f410/curiosity_log.jsonl` | **PRESERVE_ON_BRANCH_ONLY** | Historical event log. |
| `docs/runs/curiosity/6b766421f410/teacher_packs/*.json` | **PRESERVE_ON_BRANCH_ONLY** | Per-cell teacher packs from a real run. |
| `docs/runs/phase8_5_curiosity_session_state.json` | **PRESERVE_ON_BRANCH_ONLY** | Phase 8.5 session state. |

## Vocabulary mapped from Phase 8.5 → Phase 18B

| Phase 8.5 concept | Phase 18B mainline equivalent |
| --- | --- |
| `GAP_TYPES = (missing_solver, improvement_opportunity, ...)` | Phase 18B's `family_kind` is the six-family low-risk allowlist; the Phase 8.5 gap-types map to `verdict` outcomes (e.g., `missing_solver` → `ALLOWLISTED_SOLVER_SPEC` if family allowlisted, otherwise `OUT_OF_FAMILY_REJECTED`; `meta_solver_opportunity` → `BUILDER_HANDOFF_QUARANTINED`). |
| `NEXT_ACTIONS = (propose_solver, improve_solver, ...)` | Phase 18B emits a deterministic spec for the `propose_solver` case (low-risk allowlist) and a quarantined handoff for the rest. `improve_solver`, `propose_bridge`, `propose_subdivision`, `propose_meta_solver`, `clarify_routing` are recorded as `BUILDER_HANDOFF_QUARANTINED` payload metadata so a future session can act on them. |
| `EVIDENCE_HIGH_MIN = 8`, `EVIDENCE_MEDIUM_MIN = 3` | Phase 18B's `GapMiningConfig.min_signals_for_candidate = 2` (smaller threshold appropriate for the more focused fixture) + `min_confidence = 0.55`. |
| `LATENCY_FALLBACK_HIGH_MS = 8000`, `LATENCY_FALLBACK_LOW_MS = 1500` | Not directly used in Phase 18B's mainline path (latency-driven gap detection is upstream of the Phase 18B input — provided externally via `confidence_hint` and `risk_label` on each signal). |
| Per-cell teacher packs | Phase 18B emits per-candidate solver specs as JSON. A future session could group these per cell if needed. |
| Pinned artifact set with SHA-256 manifest | Phase 18B emits SHA-256 over `feature_dict` for deterministic `candidate_id`. Pinning the artifact set at session start is not needed because the proof harness self-contains the fixture. |

## Files written into Phase 18B mainline

| Mainline path | Source verdict | Notes |
| --- | --- | --- |
| `waggledance/core/autonomy_growth/gap_mining.py` | REIMPLEMENT_SMALLER_MAINLINE | New, ~250 LOC stdlib-only. |
| `waggledance/core/autonomy_growth/gap_candidate.py` | REIMPLEMENT_SMALLER_MAINLINE | New, dataclasses + StrEnum. |
| `waggledance/core/autonomy_growth/gap_training_data.py` | REIMPLEMENT_SMALLER_MAINLINE | New, solver-spec construction + training-example bundling. |
| `tools/run_phase18b_gap_miner_feedback_proof.py` | New | ~300 LOC, deterministic synthetic fixture + proof harness. |
| `tests/autonomy_growth/test_phase18b_gap_miner_feedback.py` | New | ≥17 tests covering all 6 verdicts + invariants + carry-forward. |

## Source-availability check

* `tools/gap_miner.py` — present at `origin/phase8.5/curiosity-organ:tools/gap_miner.py`. Verified by `git show`.
* `tests/test_gap_miner.py` — present.
* `tests/fixtures/gap_miner_sample/*` — present.

No `BLOCKED_MISSING_SOURCE` items. The reconciliation is complete.
