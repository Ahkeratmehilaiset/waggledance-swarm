# 2026-04-28 — Cutover Model Classification (Phase 10 P1)

> Formal classification of the v3.6.0 atomic flip operation against the four cutover models defined in `wd_phase10_master_prompt_rewritten.md` P1.

## Verdict

**v3.6.0 shipped behavior: `MODEL_C_NOOP_ALREADY_COMPLETE`.**
**Future Stage-2 flip mechanism: `MODEL_D_AMBIGUOUS` until an RFC closes the design gap.**

The two are not in conflict — they describe different operations against different states of the system.

## The four models

- `MODEL_A_POINTER_ROTATION` — filesystem-level pointer rotation (e.g. `os.replace()` of a `current/` symlink per cell).
- `MODEL_B_RUNTIME_CODE_CUTOVER` — code change that switches a runtime read path from old to new.
- `MODEL_C_NOOP_ALREADY_COMPLETE` — cutover is a no-op because either already done or there is nothing to flip.
- `MODEL_D_AMBIGUOUS` — insufficient information / no defined mechanism.

## Classification matrix — v3.6.0 release

| Required for MODEL_A                        | Present?                                                                 |
|---------------------------------------------|--------------------------------------------------------------------------|
| `current/` symlink under `data/vector/<cell>/` | NO — `grep -r os.symlink` returns zero hits anywhere in tree.            |
| `os.replace()` on a runtime read pointer     | NO — `os.replace` is used only for atomic single-file writes (`cognitive_graph`, log rotation, `learning_progress.json`, settings save). The only `os.replace` near a "current" pointer is `tools/vector_indexer.py:355-367` writing `current.json` (offline tool, runtime never reads it). |
| Cell directories with rotatable contents     | Partial — `data/faiss_staging/` has cell directories but is unread by runtime; `data/vector/` cells exist as additive snapshot. |

| Required for MODEL_B                        | Present?                                                                 |
|---------------------------------------------|--------------------------------------------------------------------------|
| Code change in this release that switches `_DEFAULT_FAISS_DIR` or the registry's base path | NO — `core/faiss_store.py:26` is unchanged; the path is the same legacy value (`data/faiss/`). |
| Code change that flips an in-process runtime branch on a `cutover_state` table/file | NO — grep for `cutover_state|cutover_states` returns zero matches anywhere. |
| Code change that reroutes hot-path readers to a new path | NO — `hybrid_retrieval_service.py` still composes the default `FaissRegistry`. |

| Required for MODEL_C                        | Present?                                                                 |
|---------------------------------------------|--------------------------------------------------------------------------|
| `validate_cutover.py` passes                 | YES — `waggledance/tools/validate_cutover.py:94-99` `is_autonomy_primary` returns True for default `WaggleSettings`. |
| Default settings already point at the post-cutover runtime | YES — `tests/autonomy/test_runtime_cutover_config.py:78-79` `assert s.runtime_primary == "waggledance"`; default `compatibility_mode is False`. |
| Migration is purely additive                 | YES — `tools/migrate_to_vector_root.py` is idempotent; legacy tree byte-identical post-migration (`tests/test_migrate_to_vector_root.py:115` `test_apply_does_not_touch_legacy_tree`). |
| No new runtime read path to flip *to* exists | YES — `data/vector/` is dormant; `data/faiss/` is the only runtime read path. |

→ **MODEL_C** matches v3.6.0 shipped behavior precisely. The autonomy cutover (#1) defaults are already on; the vector-root migration is additive; nothing else needs to flip because the new tree is unread by the runtime.

## What about the future Stage-2 flip?

The future Stage-2 cutover described in `docs/architecture/MAGMA_FAISS_SCALING.md §2` and `docs/architecture/MAGMA_VECTOR_STAGE2.md` is conceptually MODEL_B (runtime read-path code change) routed through a `current.json` pointer file. But the **mechanism is not implementable in current detail**:

- `MAGMA_FAISS_SCALING.md §2` lists triggers (campaign complete ✅, write-rate threshold ⏳, sustained latency ⏳) but does not specify the cutover code change.
- `MAGMA_VECTOR_STAGE2.md:98-100` describes a `current.json` pointer mechanism, but the runtime never reads it today (only `tools/vector_indexer.py:373-381` does).
- No runtime code branches on a "cutover_state" — there is no in-memory or on-disk handle that a future flip can write to and trigger the rerouting.

Therefore for the Stage-2 flip operation at the artifact level: **MODEL_D_AMBIGUOUS** — insufficient information / no defined mechanism. An RFC would need to close: (a) does the runtime read `current.json` directly, or via a path resolver, (b) which subsystems flip in what order, (c) atomicity contract across multiple readers, (d) rollback procedure if cell N's index is corrupt.

## Why the prior `HUMAN_APPROVAL.yaml` was correctly SUPERSEDED

The prior approval rationale described a per-cell `os.replace()` on `current/` symlinks across 8 cells. That operation is impossible against the actual repo state because:

1. No `current/` symlinks exist (verified `grep -r os.symlink` zero hits).
2. The 8-cell `data/vector/` tree exists as an additive snapshot the runtime does not read.
3. `data/faiss/` (the actual runtime read path) is not a hex-cell tree — it has `agent_knowledge`, `axioms`, `bee_knowledge`, `training_pairs`, none of which are hex cells.

Three different things were conflated:

| Cutover # | What it would do                                                              | Status                                                                                       |
|-----------|-------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| #1 Autonomy runtime cutover | Activate `waggledance/` package as primary runtime, `compatibility_mode=False` | DONE by default settings (verified `test_runtime_cutover_config.py:78-79`).                  |
| #2 FAISS hex-cell read-path cutover | Make runtime read hex-cell FAISS indices from a single canonical location | Not done. Also not strictly needed yet because runtime doesn't read hex-cell-FAISS at all (only `data/faiss/{agent_knowledge,axioms,bee_knowledge,training_pairs}`). |
| #3 Stage-2 durable-bus cutover | When JetStream durable bus + vector-indexer consumer come online, drive index rebuilds from `vector.commit_applied` events | Not done. Mechanism not specified at code level. MODEL_D for the artifact.                  |

The prior approval intended #2 or #3 with a #1-shaped mechanism. Disposition: SUPERSEDED, preserved verbatim for audit, see `docs/atomic_flip_prep/HUMAN_APPROVAL.yaml` header note and `docs/journal/2026-04-27_atomic_flip_analysis.md`.

## Implications for downstream phases

- **P11 (atomic flip prep)**: under MODEL_C for v3.6.0 the right action is to **document the no-op truth** in `docs/atomic_flip_prep/00_README.md` (already done) and **update `PROMPT_2_INPUTS_AND_CONTRACTS.md`** to reflect that the future flip is MODEL_D pending an RFC. No `HUMAN_APPROVAL_V2.yaml.draft` is needed yet because the Stage-2 mechanism is undefined.
- **P2 (control-plane / data-plane)**: once a path resolver exists, MODEL_B becomes implementable — the resolver becomes the runtime branch point. Adding a resolver therefore creates the seam a future flip needs, without performing the flip.
- **P7 (tests)**: needs both a "MODEL_C documentation test" (assert the no-op claim is grounded) and a "no-pointer-rotation invariant" test (assert no `os.symlink` and no runtime branch on cutover_state for v3.6.0).

## Evidence summary (file:line)

- `core/faiss_store.py:26` — `_DEFAULT_FAISS_DIR = ... "data" / "faiss"`
- `waggledance/bootstrap/container.py:335-338` — `FaissRegistry()` default
- `waggledance/tools/validate_cutover.py:94-99` — `check_runtime_mode` asserts in-process flag
- `tests/autonomy/test_runtime_cutover_config.py:78-79` — default `runtime_primary == "waggledance"`
- `tools/migrate_to_vector_root.py:7-9` — module header explicitly states "the physical switchover ... is a separate reviewed commit"
- `tests/test_migrate_to_vector_root.py:115,138` — additive + idempotent invariants
- `tools/vector_indexer.py:355-367` — `_swap_current_pointer` (offline tool only)
- `docs/atomic_flip_prep/00_README.md:3` — "PREPARATION ONLY"
- `CURRENT_STATUS.md:13` — "review-only release"
- Grep `os\.symlink` over repo: 0 hits (confirms absence of symlink mechanism)
- Grep `cutover_state|cutover_states|CUTOVER_STATE` over repo: 0 hits (confirms absence of runtime branch)
