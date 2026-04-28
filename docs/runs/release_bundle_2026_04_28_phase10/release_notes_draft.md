# Phase 10 — Foundation, Truth, Builder Lane (v3.6.x post-release substrate)

> Substrate work on top of v3.6.0. No runtime hot-path mutation. No atomic flip. PR-only path; merge gated on green CI.

## TL;DR

* New `waggledance/core/storage/` package: 16-table SQLite control plane + path resolver. The seam future cutovers can use without code changes.
* New `waggledance/core/providers/` package: execution layer on top of Phase 9 routing. Real `ClaudeCodeBuilder` subprocess lane (RULE 17 disciplined; isolated worktree; bounded timeout; JSONL invocation log; dry-run fallback when CLI absent).
* New `waggledance/core/solver_synthesis/{cold_shadow_throttler, llm_solver_generator, solver_bootstrap, family_specs}`: U1→U3 escalation orchestrator with throttling.
* New `waggledance/ui/hologram/scale_aware_aggregator.py`: Reality View aggregations from the control plane — no per-solver lists at 10k+ scale.
* Storage / cutover truth audit grounding the v3.6.0 atomic-flip classification: **MODEL_C_NOOP_ALREADY_COMPLETE** for v3.6.0 shipped behavior; **MODEL_D_AMBIGUOUS** for future Stage-2 flip until an RFC defines the mechanism.
* `LICENSE-CORE.md` now lists 19 new Phase 10 protected files with Change Date `2030-12-31`.

## What this enables

* A future Stage-2 flip becomes a single transactional row update in `runtime_path_bindings`. No `os.replace()`, no symlink rotation, no per-cell loop.
* WD can be *taught* through Claude Code, Anthropic, GPT, and local models from day 1 via a validated request/response contract; mentor output is locked to advisory IR objects at the API surface.
* Solver growth path scales family-first: declarative compile when family-match confidence is high; LLM free-form synthesis when low; cold/shadow throttling caps admission so the validators never see a flood at 10k+ candidate scale.

## What this does **not** do

* Does not execute any runtime cutover. Atomic flip remains a separate Prompt 2 risk domain (RULE 18).
* Does not collect HUMAN_APPROVAL interactively (RULE 9 one-shot approval).
* Does not change `core/faiss_store.py:26 _DEFAULT_FAISS_DIR`. The runtime still resolves through the legacy constant; the `PathResolver` is drop-in compatible until subsystems opt in.
* Does not enforce MAGMA append-only beyond convention. Hardening pass deferred.
* Does not ship real Anthropic / OpenAI HTTP adapters. The `dry_run_stub` adapter is the default.
* Does not promote any solver. Phase 9 promotion ladder + human gate own that.
* Does not touch any `phase8.5/*` branch (RULE 12).

## Test outcome

72 passing + 1 skipped Phase 10 targeted tests in 5.19 s:

| Suite | Pass |
|---|---|
| `tests/storage/` | 21 |
| `tests/providers/` | 18 |
| `tests/solver_synthesis/` | 12 |
| `tests/ui_hologram/` | 8 |
| `tests/phase10/` | 13 (+ 1 skipped) |

The 1 skipped test asserts the SUPERSEDED flag on `docs/atomic_flip_prep/HUMAN_APPROVAL.yaml`; that file is on `docs/post-v3.6.0-flip-analysis` and is brought forward to phase10 in P11 of the master prompt session.

## Files added

* 19 source modules under `waggledance/core/storage/`, `waggledance/core/providers/`, `waggledance/core/solver_synthesis/`, `waggledance/ui/hologram/`
* 4 architecture documents under `docs/architecture/`
* 2 truth journal documents under `docs/journal/`
* 6 test files under `tests/storage/`, `tests/providers/`, `tests/solver_synthesis/`, `tests/ui_hologram/`, `tests/phase10/`
* `LICENSE-CORE.md` updated with three new Phase 10 sections

## Files corrected

* `CURRENT_STATUS.md`: main SHA fix `a1c4152` → `8bf1869` + Phase 10 in-flight section
* `README.md`: version badge + Phase 10 section
* `CHANGELOG.md`: `[Unreleased — Phase 10]` entry
* `docs/architecture/MAGMA_FAISS_SCALING.md §0`: vector_events producer status nuance
