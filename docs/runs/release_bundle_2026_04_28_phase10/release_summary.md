# Phase 10 Release Bundle — Foundation, Truth, Builder Lane

**Branch:** `phase10/foundation-truth-builder-lane`
**Branched from:** `origin/main` @ `8bf1869` (post-v3.6.0 truthfulness commit)
**Branch tip:** `8cd33a2` (8 commits ahead of origin/main)
**Bundle date:** 2026-04-28
**Session prompt:** `wd_phase10_master_prompt_rewritten.md`
**Active model:** `claude-opus-4-7` (RULE 1 — strongest Opus available)
**Subagent model:** inherited Opus 4.7 (Explore agents used in P1)

## Scope summary

Substrate work on top of v3.6.0. **Eight commits, 8 phases (P0–P7)**. No runtime hot-path mutation. No atomic flip. No phase8.5 branch touched (RULE 12 honoured).

| Phase | Commit | Subject |
|---|---|---|
| P0 | `a3d7142` | `chore(state): bootstrap phase10 state and audited reality` |
| P1 | `b31e2fa` | `docs(audit): storage/runtime truth across core + waggledance` |
| P2 | `65d1b29` | `feat(storage): control-plane DB + path resolver + LICENSE-CORE.md` |
| P3 | `81a02fa` | `feat(provider): provider plane + builder lanes + LICENSE-CORE.md` |
| P4 | `e688ceb` | `feat(synthesis): family compiler / synthesis plumbing + LICENSE-CORE.md` |
| P5 | `8e3d1f1` | `feat(reality): truthful scalable Reality View aggregator` |
| P6 | `5a98aad` | `docs(truth): README / CURRENT_STATUS / CHANGELOG / MAGMA truth` |
| P7 | `8cd33a2` | `test(phase10): targeted truth / regression / no-leak tests` |

## Key deliverables

### Code (19 new crown-jewel modules, all BUSL-1.1, Change Date 2030-12-31)

* `waggledance/core/storage/` — control plane (SQLite, 16 tables), path resolver, registry queries
* `waggledance/core/providers/` — execution layer on Phase 9 routing scaffold; ClaudeCodeBuilder subprocess lane
* `waggledance/core/solver_synthesis/{cold_shadow_throttler,llm_solver_generator,solver_bootstrap,family_specs}` — U1→U3 escalation orchestrator
* `waggledance/ui/hologram/scale_aware_aggregator.py` — control-plane-driven aggregated panels

### Documentation

* `docs/journal/2026-04-28_storage_runtime_truth.md` — file:line truth audit
* `docs/journal/2026-04-28_cutover_model_classification.md` — formal MODEL_C / MODEL_D classification
* `docs/architecture/CONTROL_PLANE_AND_DATA_PLANE.md`
* `docs/architecture/PROVIDER_PLANE_AND_BUILDER_LANES.md`
* `docs/architecture/SOLVER_BOOTSTRAP_AND_SYNTHESIS.md`
* `docs/architecture/REALITY_VIEW_TRUTH_AND_SCALE.md`

### Truthfulness corrections

* `CURRENT_STATUS.md`: main SHA `a1c4152` → `8bf1869` (TD-P0-001)
* `README.md`: version badge updated; Phase 10 section added
* `CHANGELOG.md`: `[Unreleased — Phase 10]` entry
* `docs/architecture/MAGMA_FAISS_SCALING.md §0`: vector_events producer status tightened — offline tools ARE producers

### Tests

* 19 P2 (control-plane + path resolver)  — actually 21
* 18 P3 (provider plane)
* 12 P4 (solver bootstrap + throttler)
* 8 P5 (scale-aware Reality View aggregator)
* 13 P7 (truth / regression / license / no-leak)
* **Total: 72 passing, 1 skipped, in 5.19 s**

Files:
* `tests/storage/test_control_plane.py`, `test_path_resolver.py`
* `tests/providers/test_provider_contracts.py`, `test_provider_plane.py`
* `tests/solver_synthesis/test_solver_bootstrap.py`
* `tests/ui_hologram/test_scale_aware_aggregator.py`
* `tests/phase10/test_truth_regression.py`

## Cutover model classification (from P1)

* **v3.6.0 shipped behavior:** `MODEL_C_NOOP_ALREADY_COMPLETE`. Default settings already point at the post-cutover runtime; the `data/vector/` migration is purely additive; no `current/` symlinks; no runtime branch on cutover_state.
* **Future Stage-2 flip:** `MODEL_D_AMBIGUOUS` until an RFC defines the mechanism. The seam exists in the new `PathResolver` + `runtime_path_bindings` table — flipping a binding flips the resolution. No runtime code change required when subsystems opt in.

Atomic flip is **not executed** in this session. Phase 10 RULE 18 honoured.

## Hard rules respected

* **RULE 2 PR-only** — no direct push to main attempted.
* **RULE 3 No force / no rewrite** — phase10 branch is linearly descended from origin/main (verified by `tests/phase10/test_truth_regression.py::test_phase10_branch_history_is_linear_descended_from_main`).
* **RULE 6 BUSL Change Date 2030-12-31** — every new crown-jewel file carries the explicit per-file Change Date header AND is listed in LICENSE-CORE.md AND was added in the same commit that updated LICENSE-CORE.md.
* **RULE 8 Provider budget** — zero external API calls used. All work performed by the active session and Explore subagents (no provider invocations against the Phase 10 prompt's per-section budget).
* **RULE 12 Phase 8.5 isolation** — read-only. No phase8.5 branch operations.
* **RULE 14 Fail-loud** — `error_log.jsonl` initialized; one truth discrepancy (TD-P0-001) recorded and resolved in P6.
* **RULE 17 Builder lane subprocess** — `ClaudeCodeBuilder` is the only authorised subprocess in the new code; isolated worktree only, bounded timeout, JSONL invocation log, dry-run fallback when CLI absent.
* **RULE 18 Atomic flip is separate** — no runtime cutover, no HUMAN_APPROVAL collection, no flip PR merge.

## Local readiness

* Branch is locally green: 72/72 Phase 10 targeted tests pass.
* Working tree clean modulo three pre-existing untracked files from the prior `docs/post-v3.6.0-flip-analysis` branch (intentionally NOT carried into phase10):
  * `WD_release_to_main_master_prompt.md`
  * `docs/atomic_flip_prep/03_HUMAN_APPROVAL.yaml`
  * `docs/runs/phase9_pr_body.md`

## Remote readiness

Push attempt is the next operation (P9). The previous v3.6.0 session reported that the Claude Code shell silently backgrounded `git push` and produced no output; per RULE 10 (stop-and-handoff) this Prompt 1 will attempt **one** plain push with a 180-s timeout and fall through to operator handoff if it does not return cleanly.

## Cross-references

* `docs/runs/release_to_main_state.json` — full Phase 10 state (snapshot at squash time; preserved verbatim).
* `docs/runs/release_to_main_state_v3.6.0.json` — archived prior session state.
* `docs/runs/provider_invocations.jsonl` — provider budget log (RULE 8).
* `docs/runs/error_log.jsonl` — fail-loud structured error log (RULE 14).
* `docs/runs/release_bundle_2026_04_27/` — v3.6.0 release bundle (preserved unchanged).
* `docs/runs/post_phase10_finalize_2026_04_28/session_state.json` — post-merge truth / governance / RFC finalization session state.

## Post-merge addendum (2026-04-28T12:14:15Z)

* PR #54 squash-merged to `main` after green CI.
* Squash commit on `main`: `08b7e8c6ea589e699a7fb8d0cb600332aaec12d5`.
* Pre-squash branch tip preserved on `phase10/foundation-truth-builder-lane`: `24ef97ea7d42e56a9575b0dd658832a23099a7fb`.
* Tag decision: **Option A** chosen (no new SemVer tag at squash time). An optional `v3.6.1-substrate` prerelease tag may be added in the follow-up `post-phase10/finalize-truth-rfc-governance` PR after CLAUDE.md governance and the Stage-2 cutover RFC land.
* `docs/runs/release_bundle_2026_04_28_phase10/merged_commit_sha.txt` records the squash SHA verbatim.
