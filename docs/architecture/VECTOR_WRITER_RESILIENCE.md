# Vector writer resilience

Phase 8.5 R7.5 hardening characterization. Crown-jewel area:
`waggledance/core/magma/vector_events.py` and `tools/vector_indexer.py`.

This document maps every known failure surface in the Stage-2 atomic
writer + event log scaffold, states the guarantee level (exact_once
/ at_least_once_idempotent / best_effort) for each, and links each
surface to the test that exercises it. **Runtime still does not read
this scaffold.** Repointing is gated until after R7.5's guarantees
are characterized — which is the purpose of this doc.

## Chosen event semantics contract

```
chosen_event_semantics = at_least_once_but_idempotent_per_commit
```

For both:

- **commit_applied event emission**, and
- **checkpoint advancement**.

A given logical commit (faiss_commit_id) may produce 1 or 2
`vector.commit_applied` events under bounded crash scenarios. The
duplicates share `faiss_commit_id`, `checksum`, `vector_count`, and
`source_events`. The `input_event_range` field can shift on the
duplicate because the first pass's just-emitted commit_applied is
itself an event in the log when the second pass folds.

**Idempotency is at the commit-content level, not the event-identity
level.** Consumers that need observed-once semantics must dedup by
`faiss_commit_id` or by event_id within a sliding window.

## Convergence after failure

Definition (R7.5 Core Resilience Principle): after a bounded
crash/failure during apply, rerunning the writer/indexer must
converge to the same final committed state as a clean run, without
corrupting current state.

This is demonstrated end-to-end by
`test_writer_convergence_after_bounded_failure` (test #24): same
input event log → clean run vs chaos run with three injected failure
points (fail-before-swap + mid-checksum + clean rerun) → byte-
identical commit_id and signatures.

## Failure matrix

| # | Failure point                                  | Expected behavior                                                      | Guarantee                          | Tests covering        |
|---|------------------------------------------------|-------------------------------------------------------------------------|------------------------------------|------------------------|
| 1 | Failure mid artifact write (manifest/vectors)  | Prior `current.json` untouched; partial dir overwritten on retry       | at_least_once_idempotent           | test #3                |
| 2 | Failure mid checksum                           | Prior commit untouched; retry recomputes                                | at_least_once_idempotent           | test #4                |
| 3 | Failure before swap                            | Prior pointer untouched; staged dir present but unreferenced            | at_least_once_idempotent           | test #5                |
| 4 | Failure after swap, before commit_applied emit | Pointer advanced; rerun emits the event for the first time              | at_least_once_idempotent           | test #6                |
| 5 | Failure after emit, before checkpoint save     | Pointer advanced; event in log; rerun emits a duplicate commit_applied  | at_least_once_idempotent           | test #7                |
| 6 | Rerun after partial failure                    | Same final commit_id and pointer                                        | at_least_once_idempotent           | tests #6, #7, #9, #24  |
| 7 | Repeated replay of same event window           | Identical projection, no new commit                                     | exact_once on projection           | tests #8, #15          |
| 8 | Idempotent rebuild after crash (ckpt wiped)    | Same commit_id from event log alone                                     | at_least_once_idempotent           | test #9                |
| 9 | Malformed event log rows                       | Silently skipped; later valid rows still apply                          | best_effort (skip)                 | tests #2, #17          |
| 10 | Per-cell isolation                            | One failing cell does not block other cells                             | guaranteed                         | test #10               |
| 11 | Identical projection content across cells     | Each cell has its own commit_id (cell_id is part of the hash input)     | guaranteed                         | test #11               |
| 12 | `current.json` rewrite                        | tmp + os.replace in the same dir → atomic rename                        | guaranteed (modulo fsync)          | test #12               |
| 13 | Multi-writer append hazard                    | Bytes can interleave outside text-mode line boundaries on POSIX         | best_effort (single-writer assumed)| test #16 (deterministic sim) |
| 14 | Truncation/shrink of stray `.current.*.tmp`   | Ignored; subsequent atomic swap writes a fresh tmp                       | guaranteed                         | test #18               |
| 15 | Ordering: stage → swap → emit → checkpoint    | Pointer is at new commit_id at the moment emit fires                    | guaranteed                         | test #19               |

## Pointer (`current.json`) durability semantics

`_swap_current_pointer` uses `tempfile.NamedTemporaryFile(dir=cell_dir)`
followed by `os.replace(tmp, cell_dir / "current.json")`.

| Property                                                | Status                       |
|---------------------------------------------------------|------------------------------|
| Atomic rename on POSIX                                  | Guaranteed (Python docs)     |
| Atomic rename on Windows                                | Guaranteed (since 3.3)       |
| Tmp file lives in the same directory as the destination | Yes — required for atomicity |
| `fsync(fd)` before close                                | **Not performed**            |
| `fsync` of containing directory after replace           | **Not performed**            |
| Survives kernel-level disk loss between replace and physical commit | **best_effort** (no guarantee) |

The omission of fsync is deliberate: portable cross-platform fsync +
directory-sync hardening introduces meaningful Windows/POSIX
abstraction debt, and current.json contents are recoverable from
the event log + commit dirs anyway. **A hard power-loss between
swap and physical disk commit is in the best_effort envelope.**
Document this explicitly in any future runtime-flip review.

## Multi-writer append hazard

`vector_events.emit()` opens the JSONL file in append mode, writes
one full line + `\n`, and closes — no `fcntl.flock`, no
`msvcrt.locking`, no advisory lock. The current scaffold assumes a
single writer process at a time.

Behavior under concurrent appenders:

- **POSIX**: kernel guarantees atomic appends only up to PIPE_BUF
  (typically 4096 bytes). Python text-mode buffering can defeat this
  — partial-line interleave is possible if two processes write
  simultaneously.
- **Windows**: `FILE_APPEND_DATA` semantics make a single-write
  append atomic, but text-mode buffering still applies.

The scaffold mitigates partial-line interleave via the read path:
`vector_events.read_events` silently drops any line whose JSON parse
fails (test #2). A corrupted partial line is therefore lost — not
attributed to the wrong projection.

We do not add OS-level concurrent-writer tests because they are
inherently flaky. Test #16
(`test_multi_writer_deterministic_interleaving_simulation`) drives
the interleave deterministically by manually constructing the
broken-line scenario and verifying that `read_events` skips the
malformed half-line.

**Recommendation**: add a `flock`-style portable lock helper in
Stage 2.5 (or move to JetStream / NATS for true multi-writer
correctness). Out of scope for R7.5.

## Malformed event row policy

**Current behavior**: `vector_events.read_events()` calls `json.loads`
on each non-empty line; any `JSONDecodeError`, `KeyError`, or
`ValueError` (from `VectorEvent.__post_init__` validation) is caught
and the line is skipped silently. There is no warning emit, no
quarantine file, no metric counter.

**Why this is acceptable in Stage 2**: there is no live consumer.
The replay path can lose a corrupt row without affecting any user-
visible system, and the projection remains a deterministic function
of the surviving valid rows.

**Why it must change for Stage 2.5**: once the runtime flips to
read `data/vector/`, a silent-drop policy hides corruption. Stage
2.5 should:

1. Add a strict mode flag (`WAGGLE_VECTOR_STRICT_READ=1`) that
   raises on any malformed row instead of skipping.
2. Land an explicit metric: `vector_events_skipped_total{reason}`.
3. Quarantine malformed rows to
   `data/vector/_malformed/<original>.<sha>.line` so an operator
   can audit.
4. Default the runtime read path to strict mode; keep the
   skip-malformed mode for migration tools only.

**Strict-mode cutover proposal**: this should land in the same
commit window as the runtime read-path repoint. Coupling the two
prevents a window where the runtime reads a silently-corrupted
projection.

## Event ordering inside `apply()`

Per cell, the apply loop executes:

```
1. _stage_commit(cell_state, new_commit_id, vroot)
2. (test-only failure injection point: _fail_before_swap_for_cells)
3. _swap_current_pointer(vroot / cell_id, new_commit_id)
4. vector_events.emit(commit_applied_event, event_log)
5. per_cell.last_applied_event_id / commit_id / applied_ts /
   vector_count are updated in memory (still inside the try block)
```

After the loop, **only if at least one cell applied**:

```
6. checkpoint.last_applied_ts = now
7. checkpoint.global_last_applied_event_id = last_eid_global
8. save_checkpoint(checkpoint, checkpoint_path)
```

A failure at step 5 mid-update is benign — the per_cell entry is in
memory only and will be rebuilt on the next run. A failure at step 8
keeps the in-memory checkpoint invisible to the next run, but the
filesystem state (commit dirs + pointer + event log) is sufficient
to rebuild it.

## What is deferred to Stage 2.5 / JetStream / locking

| Item                                  | Why deferred                                                           |
|---------------------------------------|------------------------------------------------------------------------|
| Strict malformed-row mode             | Couple with runtime read-path repoint to avoid mixed mode              |
| Portable advisory file lock           | Single-writer-assumed today; lock helper has Windows/POSIX abstraction debt |
| `fsync(fd)` and directory sync        | Cross-platform sync is non-trivial; best_effort is acceptable for off-runtime scaffold |
| Multi-writer concurrent emit          | JetStream / NATS will absorb this; no point hardening file-based variant |
| Exact-once-per-commit observation     | Requires either two-phase journal or transactional emit; not justified here |

## Acceptance summary

R7.5 acceptance criteria status:

| # | Criterion                                                   | Status |
|---|-------------------------------------------------------------|--------|
| 1 | VECTOR_WRITER_RESILIENCE.md exists and is concrete          | ✅ this file |
| 2 | Inspection recorded in state.json                           | ✅ |
| 3 | ≥ 26 resilience tests covering every category               | ✅ 26 in test_vector_writer_resilience.py |
| 4 | Clear at-least-once-idempotent contract chosen / tested     | ✅ test #13 |
| 5 | Convergence after bounded failure demonstrated              | ✅ test #24 |
| 6 | Hardening minimal / no-op confirmed                         | ✅ no-op (commit 3) |
| 7 | state.json exists and is useful                             | ✅ |
| 8 | Atomic commits on phase8.5/vector-chaos                     | ✅ 4 commits |
| 9 | Runtime untouched                                           | ✅ |
| 10 | Campaign safe (sibling worktree)                           | ✅ |
| 11 | All test writes in tmp dirs                                | ✅ test #25 |
| 12 | Malformed-row policy documented                            | ✅ this section |
| 13 | Multi-writer hazard characterized                          | ✅ this section + test #16 |
| 14 | Pointer durability documented                              | ✅ this section |
| 15 | No unrelated architecture work leaked                      | ✅ |
