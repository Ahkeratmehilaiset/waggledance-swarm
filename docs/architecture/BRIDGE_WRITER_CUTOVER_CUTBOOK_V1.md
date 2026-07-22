# Bridge Writer Cutover Cutbook v1

## Status

This document specifies a source-only evidence contract for a future bridge
writer cutover. It is deliberately dormant. The accompanying builder always
returns `HOLD_SOURCE_FOUNDATION_ONLY`; a syntactically valid report is not a
deployment receipt, an operator approval, or permission to change bridge
state.

The foundation must not stop or start processes, mutate Scheduled Tasks,
write the runtime tree, append bridge events, replay WAL files, merge a
branch, mark a change ready, or deploy. Those actions require a later
executor, complete live evidence, exact-head RCO consensus, and separate
operator authority.

## Normative source contract

The normative configuration is
`configs/bridge_writer_cutover_cutbook.v1.json` with schema
`wd.bridge_writer_cutover_cutbook_config.v1`. It fixes these invariants:

- activation state is `hold_source_foundation_only`;
- writer lock order is AppendV1, then AppendV2;
- the replayer acquires ReplayV1 as a non-blocking guard, then AppendV1 and
  AppendV2 in that order while still holding ReplayV1;
- locks are released in reverse order;
- AppendV1 and AppendV2 share one 10,000 ms deadline; ReplayV1 uses its
  separate zero-timeout guard contract;
- stream generation change, rotation, and rewrite are forbidden;
- orphan writer plus pending/final replayable WAL file and row targets are
  zero;
- every authority field is exactly `false`.

The lock identities are:

```text
Global\WaggleDanceBridgeSpoolReplayV1
Global\WaggleDanceBridgeAppendV1
Global\WaggleDanceBridgeAppendV2
```

The cutbook cannot override the writer or replayer lock contracts. It consumes
the exact-key schema `wd.bridge_writer_lock_lifecycle_receipt.v1` and rederives
a non-authoritative `lock_lifecycle_consistency` candidate. The receipt binds
the exact source head, the replayer action and provenance digest, both
quiet-start and post-drain canonical-state digests, the exact quiet interval,
the shared append deadline, an ordered event trace, and its own canonical
SHA-256.

For an `acquire` event, `at_utc` is the wait-invocation time. The reducer uses
that instant to rederive the remaining shared-deadline timeout with
microsecond-exact, upward millisecond rounding capped by the configured
10,000 ms budget. A producer reporting `timeout` must not emit cleanup or any
other following event before `at_utc + timeout_ms`; the timeout completion
bound is part of lifecycle chronology.

The producer must bind the receipt to the exact evidence head and to a
canonically hashed Scheduled Task provenance row whose entrypoint blob is
`restore-bridge-spool` at
`.agent-bridge/bin/Restore-BridgeSpool.ps1`. Resealing a foreign-head,
digest-corrupt, or different-entrypoint row cannot establish lifecycle
consistency. Timestamp strings use canonical UTC `Z` form with no more than
six fractional digits.

The event trace must prove ReplayV1 construction and zero-timeout acquisition,
then AppendV1 and AppendV2 construction/acquisition under one deadline of at
most 10,000 ms. Canonical-stream mutation may occur only after all three locks
were acquired cleanly. Cleanup is exactly once in reverse order, with release
before dispose for an acquired mutex and dispose-only for a constructed mutex
that was not acquired. Timeout, abandoned ownership, construction failure, or
an unexpected wait result forbids mutation and requires the same exact reverse
cleanup for the constructed/acquired subset. Every lifecycle timestamp is
strictly ordered inside the bound quiet interval. The receipt capture is not a
lifecycle event: it must occur strictly after the quiet interval ends and no
later than the enclosing evidence capture.

Malformed schema, key, or JSON types are contract errors. Digest, provenance,
ordering, deadline, chronology, mutation, outcome, and cleanup contradictions
are granular HOLD blockers. Even a consistent candidate is caller-supplied and
unauthenticated: `lock_lifecycle` remains exactly `false`, all authority remains
false, and `lock_lifecycle_authentication_not_implemented` remains blocking
until a sealed collector verifier exists. Current runtime cleanup warnings are
not such a verifier.

## Scope and known writer surface

The source inventory names the canonical writer/replayer components:

- `.agent-bridge/bin/Write-AgentEvent.ps1`
- `.agent-bridge/bin/Restore-BridgeSpool.ps1`
- `tools/bridge_event_writer.py`

Known direct Python callers are:

- `tools/bridge_loop_tick.py`
- `tools/idle_protocol_activate.py`
- `tools/close_bridge_rco_request.py`

Known PowerShell and wrapper callers are enumerated in the configuration.
Tests must detect drift between direct call sites and that inventory. A known
list is not proof that every process, task action, dynamic entrypoint, or open
writer handle has been discovered.

Therefore every report contains this immutable scope result:

```json
{
  "complete": false,
  "reason": "non_heuristic_process_task_scope_not_implemented"
}
```

Caller input cannot replace or upgrade that result. Production remains
`REFUSE` with exit code 3 while it is incomplete.

## Sanitized validated inventory

The deployment gate may project collected A/B observations into
`wd.bridge_runtime.validated_inventory.v1`. The projection is deterministic,
hash-only where identity data is sensitive, and bound to one exact source
commit. It may expose only:

- exact source head, host digest, boot digest, and capture times;
- manifest action identifiers and process IDs;
- hashes of process creation identity, commands, task actions/definitions,
  dependency closures, and installed blobs;
- manifest runtime entrypoint and dependency blob identifiers;
- per-action toolchain IDs plus pinned toolchain hashes and sizes;
- repository-relative source paths, SHA-256 values, and byte sizes;
- toolchain identifiers, SHA-256 values, and byte sizes;
- the canonical inventory digest and immutable incomplete scope proof.

It must not expose absolute runtime or worktree paths, command lines, tokens,
owner/principal SIDs, task names or paths, task XML, event/WAL content,
queries, or payload data. Sensitive process and task identity fields may be
inputs to `identity_sha256`, but must not be copied into the projection.

Each writer-capable process or task action must establish this chain:

```text
process identity
  -> manifest action_id
  -> command/action and closure SHA-256
  -> entrypoint and dependency runtime blob IDs
  -> repository-relative source blob
  -> installed runtime blob
  -> exact source head
```

Each action-level provenance record contains a `runtime_blobs` array keyed by
`blob_id`. The sorted `dependency_blob_ids` closure includes the entrypoint
ID, matching the deployment gate. `runtime_blobs` contains exactly one row
for every closure ID, with no duplicate or extra IDs. Each row binds that blob ID
to one repository-relative `source_path`, its source SHA-256, and the
installed runtime SHA-256; the two SHA-256 values must match.
Scheduled Task provenance also binds `definition_sha256`. Each action binds
the exact sorted toolchain-ID set and matching `{id, sha256, size}` rows from
the validated projection; changing a tool binary cannot remain green by
resealing only the projection wrapper.

The builder consumes the complete sanitized projection, not an opaque digest
alone. It validates exact keys and canonical ordering for A/B captures,
processes, Scheduled Tasks, runtime blobs, and toolchain rows; rederives the
projection digest; binds the wrapper head/host/boot/digest to the projection;
and requires every projected process/task identity to have exactly matching
action and runtime-blob provenance. Empty projected inventories or opaque
placeholder validation digests are rejected.

Each A/B `sample_sha256` is rederived from the projection's host/boot,
capture label/time, and sorted process/task identity digests exactly as the
deployment gate computes it. Runtime/tool/action definition manifests must
also be identical across pre-freeze and post-start projections under the same
exact head; resealing a drifted post-start manifest cannot remain green.

Only `exact_head_runtime_install_provenance` is admissible.
`linked_worktree`, `legacy_direct_append`, and `unknown` are terminal
blockers.

## Future quiet-window evidence

The pure builder consumes evidence; it does not collect it. A future
privileged collector/executor must supply four independently captured,
hash-bound observations:

1. `pre_freeze_inventory`
2. `quiet_start_state`
3. `post_drain_state`
4. `post_start_inventory`

Their capture timestamps are strictly increasing; equal adjacent phase times
are invalid. The post-start projection's first nested A/B capture must occur
strictly after the sealed post-drain observation; its second capture remains
bounded by the post-start wrapper time.

During a future quiet window, all ordinary writers must remain stopped. Only
the attested replayer may run. New writers may start only after the
post-drain conservation snapshot has been sealed.

Each state observation must bind:

- host and boot digests and capture time;
- canonical file identity, length, record count, and tail anchor;
- ordered canonical row SHA-256 values before and after replay, where every
  row digest covers the exact strict-UTF-8 row bytes including its terminal
  LF;
- strict UTF-8, JSONL, and LF-termination checks;
- checkpoint schema, file identity, length, and tail anchor;
- pending and final WAL file/row counts;
- ordered WAL occurrence identities
  (`source_kind`, `source_order_sha256`, `file_ordinal`,
  `wal_file_identity_sha256`, `row_index`, `row_sha256`) and their replay
  classifications;
- quarantine count and inventory digest.

The quiet-start canonical suffix list is exactly empty. Only the post-drain
observation may carry replay suffix rows.

Host/boot change, canonical rotation, generation change, truncation, rewrite,
unknown append, missing digest, torn line, blank line, non-UTF-8 data, or a
checkpoint mismatch blocks the cutover.

## Event and WAL conservation

Let `W_existing` be WAL row digests already present in the canonical stream,
and let `W_new` be the ordered unique WAL row digests not already present.
The builder must rederive, rather than trust, all of the following:

```text
post_record_count = pre_record_count + len(W_new)
post_row_sha256s[:pre_record_count] = pre_row_sha256s
post_row_sha256s[pre_record_count:] = W_new in replay order
```

The canonical file identity must remain unchanged and its length must not
decrease. Every WAL row must be classified exactly once as replayed,
deduplicated against the existing stream, or deduplicated within the WAL.
The runtime sorts final files and pending files separately, then processes all
finals before promotable pendings. Evidence therefore records `source_kind`,
a contiguous zero-based `file_ordinal`, and a hash-only
`source_order_sha256`; `replay_plan_sha256` binds the ordered file plan. Row
indexes are contiguous within each file. The builder resets the within-WAL
dedup set at each file boundary, then promotes that file's new rows into the
existing set; a repeat in a later WAL is therefore `deduped_existing`,
matching `Restore-BridgeSpool.ps1`. Pending and final replayable file counts
(`pending_file_count` and `final_replayable_file_count`) are checked as well
as row counts.

The pure builder can rederive internal replay-plan consistency and enforce
finals-before-pendings, but it cannot authenticate that caller ordinals match
the runtime's two filename-sorted lists. Consequently
`event_wal_conservation_consistency` may describe candidate math while
`event_wal_conservation` remains exactly `false` with
`wal_replay_order_attestation_not_implemented` until a sealed collector
verifier supplies that binding.
After drain, pending WAL rows and final replayable WAL rows must both be zero,
quarantine must not grow, and the checkpoint must match the new canonical
identity/length/tail state. Post-cutover orphan or legacy writer PID count
must be zero.

## Rule-10 readiness

Rule-10 is a pre-cutover evidence gate, not authority to cut over. It requires:

- 10,000 basis-point detection for every named critical held-out class;
- at least two held-out cases per critical class;
- sealed training/held-out disjointness;
- a genuinely executed rollback drill, not an eligibility-only artifact;
- recovery within one scheduler tick and 60,000 ms;
- a successful exact-head post-cutover rehearsal;
- exact-head consensus and separate operator execution authority.

A green adversarial corpus or dormant rollback classifier alone does not meet
these requirements. The six critical classes are source-fixed in the
configuration: event conservation, lock protocol, rollback/rehearsal,
runtime provenance, WAL corruption, and writer identity. The builder
rederives the candidate corpus digest and threshold result, but deliberately
keeps `checks.rule_10=false` until a sealed corpus/drill/rehearsal/consensus
verifier exists.

## Downstream production receipt gate

The production receipt window occurs after an exact deployment and dual-lock
cutover, so it cannot authorize or block the source foundation in advance.
It is reported as a downstream claim gate with `claim_safe_effect: none`.
This foundation exposes a deterministic `candidate_qualified` calculation,
but keeps `qualified=false` until a sealed receipt-index verifier exists.

The same verified per-served-event index must prove:

```text
served_total >= 10000
served_with_receipt_total * 10000 >= served_total * 9500
solver_first_served_total * 10000 >= served_total * 9500
```

Served-event identities must be unique. Receipt, solver-first, gap,
unresolved, and pending-failure identity sets must be subsets of that same
served-event denominator. Receipt and failure sets form an exact, disjoint
partition of the served set; solver-first identities are a subset of receipt
identities. In particular:

```text
0 <= served_with_receipt_total <= served_total
0 <= solver_first_served_total <= served_total
```

Gaps, unresolved rows, and pending failures remain in the denominator. The
window must occur strictly after `post_start_inventory`, bind one head,
lifecycle, clean marker and telemetry source, and carry a canonical digest of
the full receipt index. Evidence may be no older than 14 days at evaluation;
the pure builder checks internal receipt-to-report age while the future sealed
verifier must also check wall-clock freshness. Passing the candidate math
does not flip `claim_safe`.

## Builder behavior

The pure CLI accepts only:

```text
--evidence-json <path>
--json
```

It has no apply, execute, runtime-root, process, task, or output-path switch.
Invalid JSON/schema/type input exits 2. A valid report is printed and exits 3
because the decision remains `HOLD_SOURCE_FOUNDATION_ONLY`.

The report may set `lock_lifecycle_consistency=true` for internally consistent
receipt data, but it keeps authenticated `lock_lifecycle`,
`quiet_window_actor_attestation`, `event_wal_conservation`, `rule_10`, and
authenticated downstream receipt checks false. These are explicit
missing-proof states, not green booleans inferred from caller assertions.

Every report repeats exact-false authority for apply, capability grant,
deployment, Git mutation, process mutation, Scheduled Task mutation, runtime
writes, bridge appends, source writes, merge, `claim_safe` flip, and operator
approval. Input that asserts any of those authorities is rejected.

## Exit blockers

This foundation remains on HOLD until all of these exist outside this slice:

- both prerequisite source branches integrated into an exact main head;
- complete non-heuristic process/task/open-handle scope proof;
- dynamic entrypoint and worktree-origin provenance discovery;
- a trusted quiet-window/conservation collector;
- an authenticated collector binding for the hash-bound structured
  lock-lifecycle receipt and cleanup results;
- an authenticated finals-then-pendings replay-plan/order binding;
- pinned live host, toolchain, runtime, process, and task-action manifests;
- executed Rule-10 rollback and post-cutover rehearsal evidence;
- sealed Rule-10 and downstream receipt-index verifiers;
- exact-head RCO consensus and explicit operator execution authority.

No result generated by this source foundation removes those blockers.
The later production receipt window is intentionally absent from this
pre-cutover blocker list: it gates only downstream `claim_safe` evaluation.
