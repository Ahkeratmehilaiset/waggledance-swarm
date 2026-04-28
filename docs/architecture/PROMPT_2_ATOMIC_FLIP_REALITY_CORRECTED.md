# Prompt 2 — Atomic Flip Reality (Phase 10 P11 corrections)

**Status:** Phase 10 P11 corrections to the Prompt 2 contract. Composes — does not replace — `docs/architecture/PROMPT_2_INPUTS_AND_CONTRACTS.md`. No runtime cutover executed in this Prompt 1 session.

## Why this document exists

The original `PROMPT_2_INPUTS_AND_CONTRACTS.md` was authored before the 2026-04-27 atomic flip analysis (`docs/journal/2026-04-27_atomic_flip_analysis.md`) and the 2026-04-28 storage/runtime truth audit (`docs/journal/2026-04-28_storage_runtime_truth.md`). Its preconditions and invariants are still valid as a *contract template*. What it does not yet reflect:

1. The v3.6.0 atomic flip is formally classified `MODEL_C_NOOP_ALREADY_COMPLETE` (Phase 10 P1). There is nothing to flip in the v3.6.0 release — the new tree (`data/vector/`) is unread by the runtime, the legacy tree (`data/faiss/`) is read by the runtime, and no `current/` symlinks exist anywhere.
2. The future Stage-2 flip is `MODEL_D_AMBIGUOUS` until an RFC defines the *mechanism*. The triggers exist (campaign complete ✅, write-rate threshold ⏳, sustained latency ⏳ in `MAGMA_FAISS_SCALING.md §2`); the procedural steps do not.
3. Phase 10 P2 added the seam a future MODEL_B-style cutover would actually use: `runtime_path_bindings` rows in the control plane, resolved through `PathResolver`. Flipping a binding is a single transactional row update.

This document records the corrections so a future Prompt 2 session does not repeat the conflation that produced the SUPERSEDED 2026-04-27 `HUMAN_APPROVAL.yaml`.

## Three cutovers, not one

| # | Cutover | Status as of 2026-04-28 | Mechanism |
|---|---|---|---|
| 1 | **Autonomy runtime cutover** — activate `waggledance/` package as primary, `compatibility_mode=False`. | DONE by default `WaggleSettings`. Verified by `tests/autonomy/test_runtime_cutover_config.py:78-79`. | Configuration flag, no filesystem change. |
| 2 | **FAISS hex-cell read-path cutover** — make runtime read hex-cell FAISS indices from a single canonical location. | NOT NEEDED YET. Runtime does not read hex-cell-FAISS at all today (verified by source-grep in `docs/journal/2026-04-28_storage_runtime_truth.md`). When it does, the seam is `runtime_path_bindings(logical_name='default', path_kind='faiss_root')`. | Single SQLite row update once subsystems opt into `PathResolver`. |
| 3 | **Stage-2 durable-bus cutover** — JetStream durable bus + vector-indexer consumer + `vector.commit_applied` event-driven index rebuilds. | UNSPECIFIED at code level. Triggers documented in `MAGMA_FAISS_SCALING.md §2`; mechanism not yet RFC'd. | TBD — needs an RFC. |

## What the next Prompt 2 session must verify before any flip

Composes the original §1 checklist with these additions:

1. **Read `docs/journal/2026-04-28_cutover_model_classification.md`** before doing anything. Identify which of the three cutovers is being performed. Reject any approval whose rationale conflates them.
2. **Verify the operation has a target in the code.** If the rationale claims `os.replace()` on `current/` symlinks, run `grep -r "os.symlink" .` and `find data/vector -name "current"` to confirm the targets exist. If they do not, abort.
3. **Verify the runtime actually reads the new path.** A flip that points the runtime at a path the runtime does not read is a no-op masquerading as an action. Run a runtime read test (a simple `chat` call against a known query) before and after; verify the read path actually moved.
4. **Use the control plane.** A future MODEL_B flip should be a single `control_plane.bind_runtime_path(...)` call, not a per-cell loop. The seam exists; use it.

## Approval contract corrections

The original §3 approval format is still correct. Two fields need tightening:

* `rationale_paragraph` MUST cite the cutover number (#1 / #2 / #3) it is authorizing. An approval that says "atomic flip" without specifying which is rejected.
* `verification_steps_before_flip` MUST include at least one step that confirms the operation has a target (not just that triggers are met).

A V2 approval draft is NOT yet authored — there is no Stage-2 RFC, so no V2 mechanism to authorize. The 2026-04-27 SUPERSEDED `HUMAN_APPROVAL.yaml` remains preserved for audit.

## Rollback contract corrections

Original §4 stands with one addition:

* If the cutover is `MODEL_C_NOOP_ALREADY_COMPLETE`, the rollback is **also** a no-op. The Prompt 2 session in that case writes a `CutoverStateRecord(scope='...', status='noop', evidence={...})` to the control plane and exits without further action.

## What Phase 10 P11 changed in the prep directory

* No edits to `01_flip_worktree_setup.md`, `02_pre_flip_checklist.md`, `04_flip_review_bundle.md.template`, `05_post_flip_verification.md`, or `06_rollback_procedure.md`. The structural templates are correct and survive the classification correction.
* `00_README.md` carries a SUPERSEDED status block at its head (brought forward from `docs/post-v3.6.0-flip-analysis` in the post-Phase-10 finalization PR — that branch's PR #53 was superseded by it).
* The 2026-04-27 atomic flip analysis (`docs/journal/2026-04-27_atomic_flip_analysis.md`) and the SUPERSEDED `docs/atomic_flip_prep/HUMAN_APPROVAL.yaml` audit artifact (with its `APPROVAL_SHA256.txt` pin) are now on `main`, brought forward in the same finalization PR. They are preserved verbatim for audit; the YAML's header explicitly says `*** SUPERSEDED — DO NOT EXECUTE ***` and bringing it forward is preservation, not collection.
* No fresh `HUMAN_APPROVAL_V2.yaml.draft` is authored. RULE: V2 needs a defined mechanism. The mechanism is now specified in [`docs/architecture/STAGE2_CUTOVER_RFC.md`](STAGE2_CUTOVER_RFC.md), but a V2 approval template is still deferred to the actual Prompt 2 execution session — approval is one-shot and belongs to that session, not to design/build sessions.

## What Prompt 2 must explicitly NOT do

* Generate rationale text from design docs without verifying the operation targets exist in code.
* Authorize a flip across cutover numbers (#1, #2, #3) under one approval. One approval, one cutover, one specific mechanism.
* Use a per-cell `os.replace()` loop while there is a `runtime_path_bindings` seam available.
* Improvise around RULE 3 (no force / no rewrite) or RULE 9 (one-shot approval).

## Cross-references

* `docs/architecture/PROMPT_2_INPUTS_AND_CONTRACTS.md` — the original contract; this document is composed onto it.
* `docs/journal/2026-04-27_atomic_flip_analysis.md` — the analysis that flagged the conflation.
* `docs/journal/2026-04-28_storage_runtime_truth.md` — code-level truth audit.
* `docs/journal/2026-04-28_cutover_model_classification.md` — formal classification.
* `docs/architecture/CONTROL_PLANE_AND_DATA_PLANE.md` — the seam Stage-2 cutovers will use.
* `docs/atomic_flip_prep/HUMAN_APPROVAL.yaml` (on `docs/post-v3.6.0-flip-analysis`) — SUPERSEDED prior approval, preserved for audit.
