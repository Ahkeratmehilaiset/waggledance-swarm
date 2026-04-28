# Stage-2 Cutover RFC

**Status:** RFC. Authored 2026-04-28 (post-Phase-10 finalization session). Composes — does not replace — `docs/architecture/PROMPT_2_INPUTS_AND_CONTRACTS.md` and `docs/architecture/PROMPT_2_ATOMIC_FLIP_REALITY_CORRECTED.md`. Closes the design gap that left the future Stage-2 flip classified as `MODEL_D_AMBIGUOUS` in `docs/journal/2026-04-28_cutover_model_classification.md`.

**Non-goal of this RFC:** the Stage-2 cutover is **not executed** by this RFC. Execution belongs to a separate Prompt 2 session that signs a fresh one-shot human approval against this mechanism. This document specifies how that session must work.

## Why this RFC exists

After v3.6.0 shipped the autonomy fabric scaffold and Phase 10 landed the storage / provider / synthesis substrate on `main`, the future Stage-2 cutover (the runtime read-path migration that lets WaggleDance read from the new `data/vector/` tree once subsystems opt into `PathResolver`) had no implementable mechanism. The 2026-04-27 SUPERSEDED `HUMAN_APPROVAL.yaml` failed exactly because it described a per-cell `os.replace()` loop on `current/` symlinks that do not exist.

This RFC fixes that. It specifies the cutover as a single transactional row update against `runtime_path_bindings` in the control plane, gated on subsystem opt-in to `PathResolver`, gated on measurable readiness conditions, and gated on a fresh signed approval whose rationale references this RFC by name.

## 1. Mechanism

The cutover is a single transactional row update in `control_plane.runtime_path_bindings`. It is **not** a filesystem rotation, **not** an `os.replace()` loop, **not** a per-cell symlink rotation, **not** a `git push <release>:main`.

### 1.1 The row update

```python
# Pseudocode — actual call goes through ControlPlaneDB
control_plane.bind_runtime_path(
    logical_name="default",
    path_kind=LogicalPathKind.FAISS_ROOT.value,
    physical_path="data/vector",
)
```

Implementation contract (already shipped in `waggledance/core/storage/control_plane.py`):

* Deactivates the previous active binding for `(logical_name, path_kind)` and inserts the new active binding within a single SQLite transaction.
* The unique partial index on `(logical_name, path_kind, is_active)` enforces "at most one active binding" at the schema level.
* Writes a `cutover_states` row in the same transaction recording the scope (e.g. `"faiss_root"`), `from_value` (current physical path), `to_value` (new physical path), `actor` (the human approver's id), `status` (`"applied"`), and `evidence_blob` (sha256 of the signed approval YAML).
* Emits an event row to MAGMA's audit log with `cutover_applied` event_kind and the same evidence hash.

### 1.2 Why MODEL_B and not MODEL_A

This is `MODEL_B_RUNTIME_CODE_CUTOVER` per the four-model classification: a logical state change in the control plane that the runtime reads through `PathResolver`. It is not `MODEL_A_POINTER_ROTATION` because no filesystem pointer is rotated — `data/vector/` and `data/faiss/` both continue to exist on disk; only the active binding row changes, and the runtime resolves the new value at the next read.

### 1.3 What the runtime sees

A subsystem that has opted into `PathResolver` (see §2) calls `resolver.resolve(LogicalPathKind.FAISS_ROOT)` on every relevant read. The resolution order is deterministic:

1. Explicit override (`resolver.register_override(...)`) — used by tests and ad-hoc redirection only.
2. Active control-plane binding row in `runtime_path_bindings` — **this is what the cutover changes.**
3. Static default in `path_resolver.py:_DEFAULT_RELATIVE_PATHS` — matches the legacy hard-coded layout so an unconfigured resolver returns today's path.

A `ResolvedPath` carries its `source`. Reality View and status endpoints surface whether a subsystem is on the new binding or still on the legacy default.

## 2. Scope — which subsystems must opt in before Stage-2 can execute

The cutover is only meaningful for subsystems that read through `PathResolver`. Subsystems that still call `_DEFAULT_FAISS_DIR` or hard-code `Path("data/faiss")` will not see the binding flip — for them the cutover would be a silent no-op masquerading as an action.

### 2.1 Required opt-in set (must be true at cutover time)

The cutover MAY NOT execute until **every** entry in this list is verified by automated check inside the cutover session:

* `core/faiss_store.py` — replace `_DEFAULT_FAISS_DIR = ... "data" / "faiss"` with `PathResolver.resolve(LogicalPathKind.FAISS_ROOT)`.
* `waggledance/bootstrap/container.py` — `FaissRegistry()` instantiation reads through `PathResolver`.
* `waggledance/core/learning/hybrid_retrieval_service.py` and any other service that composes `FaissRegistry`.
* `core/hex_cell_topology` — any hex-cell FAISS retrieval composer.
* Every subsystem listed in `LICENSE-CORE.md` that owns a runtime read of vector content.

Verification command (must run inside the cutover session before §3 gate evaluation):

```
python tools/verify_path_resolver_adoption.py --strict
```

This tool does not yet exist — its specification is part of this RFC and must be written before the cutover can be scheduled.

### 2.2 Optional but recommended opt-in set

* `tools/vector_indexer.py` — offline, but adopting `PathResolver` makes the offline tools coherent with runtime layout.
* `tools/backfill_axioms_to_hex.py`.
* Test fixtures under `tests/storage/`, `tests/integration/`, `tests/autonomy/` — already use `register_override(...)`; no change needed.

### 2.3 Out of scope for Stage-2

* The autonomy runtime cutover (#1 in `2026-04-28_cutover_model_classification.md`) — already complete by default `WaggleSettings`.
* Stage-3 durable-bus cutover (#3) — JetStream / vector-indexer consumer / `vector.commit_applied` event-driven rebuilds. This is a separate future RFC.
* `phase8.5/*` producer subsystems — they ship as separate follow-up PRs; their adoption of `PathResolver` is judged independently when each lands.

## 3. Trigger gates — measurable conditions that open the gate

The cutover session MAY proceed past the §1 row update only if **all** of the following are true at session start. Each gate has a deterministic check the session must run; if any check fails, the session aborts and the operator is informed without the cutover being attempted.

| # | Gate | Check command (or equivalent) | Pass criterion |
|---|------|-------------------------------|----------------|
| G1 | Resolver adoption coverage | `python tools/verify_path_resolver_adoption.py --strict` | Exit 0; required opt-in set in §2.1 fully covered |
| G2 | 400h gauntlet campaign final | inspect `docs/runs/ui_gauntlet_400h_*/final_400h_summary.md` | Total ≥ 400h; HOT / WARM / COLD targets met or explicitly accepted as deferred in the cutover approval rationale |
| G3 | Soak result for current substrate | Phase 10 targeted suite + a fresh ≥ 24h soak on `main` post-resolver-adoption | 0 silent failures; `error_log.jsonl` clean over the soak window |
| G4 | Ledger drain | `data/faiss_delta_ledger/` (if non-empty) is processed or explicitly accepted as legacy in the cutover rationale | No unprocessed ledger entries that would otherwise be lost |
| G5 | Target exists | The `to_value` directory (e.g. `data/vector`) exists, has the expected hex-cell layout, and `tests/test_migrate_to_vector_root.py::test_apply_does_not_touch_legacy_tree` passes against the current `main` | Layout verified |
| G6 | Replay success | A pre-cutover replay of the last 24h of MAGMA against the new path returns identical answers within tolerance for the regression-tested query set | Replay diff = 0 on the canary set |
| G7 | Phase 8.5 disposition | All five `phase8.5/*` follow-up PRs are merged OR explicitly accepted as deferred in the cutover approval rationale | Disposition recorded for each branch |
| G8 | CI green | All required checks green on the `main` tip the cutover session checks out | All checks `SUCCESS` |
| G9 | No open in-flight PR rewrites the same surface | No open PR modifies `waggledance/core/storage/`, `core/faiss_store.py`, or `waggledance/bootstrap/container.py` between approval-signing and row-update | Confirmed via `gh pr list --state open` query inside the session |

The session records the result of every gate check in its state file (`docs/runs/stage2_cutover_<date>/session_state.json`) before evaluating the next gate. A failed gate aborts the session without further work.

## 4. Verification — proofs the cutover actually moved the runtime

The session MUST collect all three proofs before declaring the cutover successful. Any failure aborts the session and triggers the §6 rollback contract.

### 4.1 Target-exists check

Run after the row update:

```
ls -la <to_value>/                        # directory exists
python -c "from waggledance.core.storage.path_resolver import PathResolver, LogicalPathKind; \
  r = PathResolver(control_plane=cp); print(r.resolve(LogicalPathKind.FAISS_ROOT))"
```

The resolver MUST return the new `to_value` and `source='control_plane'`. If it returns the static default, the row update did not commit — abort.

### 4.2 Runtime read-path proof

Run a runtime read against a known canary query before the cutover and again after, comparing read-path metadata:

```
python tools/runtime_read_path_probe.py --query "<canary>" --before-cutover-snapshot <path>
python tools/runtime_read_path_probe.py --query "<canary>" --after-cutover-snapshot <path>
```

The before snapshot's `read_path` field MUST be the legacy path. The after snapshot's `read_path` field MUST be the new path. If they are equal, the runtime is not on `PathResolver` — the cutover claims to have done something it did not. Abort and roll back.

### 4.3 MAGMA / FAISS / control-plane coherence

Verify that:

* MAGMA's `cutover_applied` event row exists with `evidence_blob == sha256(HUMAN_APPROVAL_V2.yaml)`.
* `runtime_path_bindings` shows exactly one row with `(logical_name='default', path_kind='faiss_root', is_active=1)` after the update.
* The control plane's `cutover_states` row for this scope is `status='applied'` with the same evidence hash.
* No `data/faiss/` write has occurred during the cutover window (legacy tree is now read-only de facto).

If any check fails, the `runtime_path_bindings` and `cutover_states` rows MUST be reverted in a single transaction (see §6) and the session aborts.

## 5. Approval contract V2

The Stage-2 cutover requires a NEW approval artifact. The 2026-04-27 SUPERSEDED `HUMAN_APPROVAL.yaml` does NOT authorize this cutover — its rationale described a different mechanism.

### 5.1 One-shot approval, execution-session-only

* Approval is collected ONCE, in the actual cutover execution session — never during ideation, design, RFC authoring, refactoring, or build sessions.
* No design / build / docs session may prompt the operator for approval keys, signatures, or `human_approval_id` strings against this RFC.
* Bringing forward the SUPERSEDED 2026-04-27 approval as an audit artifact is preservation; collecting a fresh approval is a distinct act and belongs only to the cutover session.

### 5.2 Required fields in `HUMAN_APPROVAL_V2.yaml.draft`

The approval template MUST be authored in the cutover session before signing, NOT in this RFC session. When authored, it MUST require:

* `approval_kind: "stage2_cutover"`.
* `cutover_number: 2` (literal `2`, never `1` or `3` or unspecified).
* `mechanism: "control_plane.runtime_path_bindings.row_update"`.
* `rfc_pinned_path: "docs/architecture/STAGE2_CUTOVER_RFC.md"`.
* `rfc_pinned_sha256: "<sha256 of this RFC at the moment of signing>"`.
* `from_value`, `to_value` — explicit physical paths.
* `gate_results: {G1: pass, G2: pass, ..., G9: pass}` — every gate from §3 with its actual outcome at signing time.
* `verification_steps_before_flip: [...]` — at least the §4 proofs.
* `rollback_contract: "docs/architecture/STAGE2_CUTOVER_RFC.md#6-rollback"`.
* `approver_signature: "SIGNED_BY_<reviewer>"`.
* `human_approval_id: "human:<reviewer>:<utc-iso-8601>"`.
* `signature_date_iso: "<utc-iso-8601>"`.
* The four structural invariants (`no_runtime_auto_promotion`, `no_main_branch_auto_merge`, `no_foundational_mutation`, `no_raw_data_leakage`) all `true`.

### 5.3 Rationale rules

The approval rationale MUST cite:

* the cutover number explicitly (`#2 FAISS hex-cell read-path cutover`),
* this RFC by file path and sha256,
* every G* gate result with timestamp.

Approvals whose rationale does not cite the cutover number, or that conflate cutover #1 / #2 / #3, MUST be rejected by the cutover session at signature-validation time.

## 6. Rollback

If §4 verification fails, OR if the cutover session detects an inconsistency between control-plane state, MAGMA event log, and runtime read path within 60 minutes of the row update, the session MUST execute the rollback before exiting.

### 6.1 Rollback row update

Single transaction:

```python
control_plane.bind_runtime_path(
    logical_name="default",
    path_kind=LogicalPathKind.FAISS_ROOT.value,
    physical_path="<from_value-recorded-in-cutover_states-row>",
)
```

Plus, in the same transaction:

* Mark the original `cutover_states` row `status='rolled_back'` with `rollback_reason` and `rollback_evidence_blob`.
* Insert a new `cutover_states` row scoped to the rollback action itself.
* Emit a MAGMA `cutover_rolled_back` event.

### 6.2 Rollback verification

After rollback, repeat §4 verification with the legacy path as the expected target. The rollback is only complete when the runtime read path returns to the original physical path AND the control-plane state is consistent.

### 6.3 Out-of-band escalation

If rollback itself fails (control plane refuses the row update, or runtime cannot resolve the legacy path), the cutover session:

1. Stops all autonomy runtime activity by setting `WaggleSettings.compatibility_mode = True` in a config override file.
2. Writes a `cutover_states` row with `status='abort_unrecoverable'` if the control plane is still writable.
3. Emits a `cutover_abort_unrecoverable` event to MAGMA.
4. Halts and does NOT attempt further automated remediation. The operator is the only authorized actor for the next step.

## 7. Non-goals

This RFC does **not**:

* Execute the cutover. Execution belongs to a separate Prompt 2 session against this RFC.
* Author `HUMAN_APPROVAL_V2.yaml.draft`. The template is authored in the cutover session, not here, because the gate results and SHAs cannot be filled in until that session runs.
* Migrate `data/faiss/` to `data/vector/`. The cutover assumes the migration is already done (Stage-1, completed 2026-04-24 by `tools/migrate_to_vector_root.py`); the cutover only flips which one the runtime reads.
* Rotate any filesystem pointer. There are no `current/` symlinks anywhere in the tree (`grep -r os.symlink .` returns zero hits) and this RFC does not introduce any.
* Touch `phase8.5/*` branches. They are read-only per CLAUDE.md §6 / Phase 10 RULE 12 unless their own follow-up PRs explicitly handle them.
* Execute the Stage-3 durable-bus cutover. That is a separate future RFC.
* Replace `docs/architecture/PROMPT_2_INPUTS_AND_CONTRACTS.md`. It composes onto it (the latter is the contract template, this is the mechanism specification).
* Authorize an atomic flip across multiple cutover numbers under a single approval. One approval, one cutover, one specific mechanism.

## 8. Foundational invariants this RFC explicitly preserves

| Plane | Owner | What this RFC does not change |
|---|---|---|
| MAGMA (history plane) | append-only event log; AuditLog / ReplayEngine / MemoryOverlay / Provenance / TrustEngine | The cutover writes one event (`cutover_applied`) and possibly one rollback event; it never deletes or mutates history. |
| Control plane (current state plane) | `ControlPlaneDB` over `waggledance/core/storage/control_plane.py`, 16 SQLite tables | The cutover updates two rows: one in `runtime_path_bindings` (deactivate-then-insert), one in `cutover_states` (insert). No schema change. |
| FAISS / Chroma (vector content plane) | `FaissRegistry` and friends | Untouched at cutover time. The flip only changes which physical root the registry resolves; the index data on disk does not move. |
| Active runtime read path | `_DEFAULT_FAISS_DIR` until subsystems opt into `PathResolver`; thereafter the resolver | The cutover does NOTHING until §2.1 opt-in is verified. After opt-in, the resolver mechanically reflects the active binding row. |

## 9. Cross-references

* `docs/architecture/PROMPT_2_INPUTS_AND_CONTRACTS.md` — the original Prompt 2 contract template.
* `docs/architecture/PROMPT_2_ATOMIC_FLIP_REALITY_CORRECTED.md` — Phase 10 corrections that classified the Stage-2 flip as MODEL_D until this RFC.
* `docs/architecture/CONTROL_PLANE_AND_DATA_PLANE.md` — the seam this RFC weaponizes (`runtime_path_bindings`, `PathResolver`).
* `docs/journal/2026-04-27_atomic_flip_analysis.md` — the analysis that flagged the conflation of cutovers #1 / #2 / #3.
* `docs/journal/2026-04-28_storage_runtime_truth.md` — code-level truth audit.
* `docs/journal/2026-04-28_cutover_model_classification.md` — formal MODEL_C / MODEL_D classification this RFC closes.
* `docs/atomic_flip_prep/HUMAN_APPROVAL.yaml` — the SUPERSEDED 2026-04-27 approval, preserved for audit; explicitly does NOT authorize the Stage-2 cutover.
* `docs/architecture/MAGMA_FAISS_SCALING.md §2` — Stage-2 design doc that this RFC implements at the mechanism level.
* `CLAUDE.md §10` — atomic-flip discipline rules that bind every Claude Code session encountering this RFC.
