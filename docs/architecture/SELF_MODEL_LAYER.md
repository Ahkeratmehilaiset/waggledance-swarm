# Self-model layer — Phase 8.5 Session B

- **Status:** scaffolding. Read-only over upstream artifacts. Runtime untouched.
- **Crown-jewel area:** `waggledance/core/magma/self_model*` and `waggledance/core/magma/reflective_workspace*`. License Change Date pinned to 2030-03-19.
- **Builds on:** Session A (gap_miner curiosity outputs).

## What this layer does

Produces a deterministic, artifact-grounded self-description of WaggleDance:

- **Scorecard** — 7 dimensions with v0.1 formulas, evidence refs, why_it_matters
- **Cells** — 8 cells classified strong / weak / under_pressure / unknown
- **Attention focus** — top-3 curiosity items by `estimated_value`
- **Blind spots** — coverage_negative_space + curiosity_silence detectors
- **Workspace tensions** — mechanical scorecard + cell drift heuristics
- **Meta-curiosity** — non-canonical when data permits
- **Self-Entity alignment** — placeholder for a future Self-Entity module
- **Invariants + ruptures** — rolling computations over the last `min(5, history_length)` snapshots
- **Continuity anchor** — `{branch_name, base_commit_hash, pinned_input_manifest_sha256}` on every emitted item

## Relation to existing Self-Entity

Today there is **no** dedicated `self_entity.py` module under `waggledance/core/magma/` or adjacent paths. The snapshot layer is forward-compatible: when a Self-Entity module lands, it should expose `export_for_snapshot()` for the snapshot to consume read-only. Until then `SelfEntityAlignment.exists=False` and `alignment_ratio=1.0`.

## What is deferred

- `dream_curriculum` runtime logic
- Dream Mode execution
- meta_learner / hive_proposes
- vector writer chaos testing
- runtime repoints
- JetStream / NATS
- learned ranker over the scorecard
- live Prometheus surfaces

## Why safe during a live campaign

1. **Read-only** over disk artifacts. No write to runtime paths.
2. **No port 8002**, no runtime adapter import.
3. **No Ollama** dependency. Tests run sub-second.
4. **Pinned input set.** Live-growing `hot_results.jsonl` is bounded to its session-start byte count; appended tail is ignored.
5. **Deterministic outputs.** Six emitted artifact families (`self_model_snapshot.json` + `.md`, `scorecard.json`, `continuity_anchors.json`, `self_model_delta.json`, `data_provenance.json`) all have byte-identity tests across reruns of the same pin.

## How this layer enables future self-improvement

1. **It can compare itself to earlier selves.** The history chain (HISTORY.jsonl with prev_entry_sha256 chaining + genesis marker `0…0`) lets the next session diff snapshots and notice what moved.
2. **It can notice it was wrong.** Calibration evidence per dimension lets the next snapshot apply `0.7 × evidence + 0.3 × prior` toward observable reality.
3. **It can record how it corrected itself.** `calibration_corrections.jsonl` is append-only and tracks dimension, prior, evidence_implied, magnitude, source_refs, correction_count_in_window.
4. **It does not overfit.** Oscillation protection at 3+ corrections dampens to `0.5/0.5`; at 5+ corrections the dimension freezes and a `calibration_oscillation` tension surfaces for human review.
5. **It exposes tensions for later sessions to act on.** Workspace tensions carry a `resolution_path` (`calibration_correction` / `blind_spot_promotion` / `deferred_to_dream` / `requires_human_review`) and a `lifecycle_status` (`new` / `persisting` / `resolved`).
6. **It gives Dream Mode a concrete consumer contract.** `HOOKS_FOR_DREAM_CURRICULUM.md` (contract_version 1) names the exact fields the next session will read.

## CLI cheat-sheet

```
python tools/build_self_model_snapshot.py --help
python tools/build_self_model_snapshot.py --dry-run
python tools/build_self_model_snapshot.py --apply
python tools/build_self_model_snapshot.py --apply --real-data-only
python tools/build_self_model_snapshot.py --apply --previous-snapshot path/to/prev.json
python tools/build_self_model_snapshot.py --apply --cell thermal
python tools/build_self_model_snapshot.py --apply --no-meta-curiosity
```

Default `--output-dir` = `docs/runs/self_model/<pinned_input_manifest_sha12>/`. Default `--history-path` = `docs/runs/self_model/HISTORY.jsonl` unless `--output-dir` is supplied (then `<out_dir>/HISTORY.jsonl`).

## Acceptance status (this branch)

- ✅ build_self_model_snapshot tool exists and works
- ✅ Real pinned upstream outputs were attempted first (Session A `docs/runs/curiosity/6b766421f410/`)
- ✅ `real_data_coverage_ratio = 0.89` (≥ 0.7 threshold)
- ✅ All six artifact families are deterministic
- ✅ Reflective workspace scaffolding concrete
- ✅ Calibration evidence path + corrections jsonl + oscillation table
- ✅ Blind spots from coverage_negative_space + curiosity_silence
- ✅ At least one tension producible from mismatch fixture
- ✅ ≥ 45 targeted tests (delivered 57: 17 + 19 + 21)
- ✅ License Change Date 2030-03-19 in same commit as first non-trivial logic
