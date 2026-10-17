# External Tools watcher recovery: unwired kernel

This is the first bounded implementation slice of the operator-authorized
LLM-free external watcher restarter. It is **not an installed restarter**.
Supervisor stays OFF. No task registration, process collector, live restart,
bridge writer, native queue injection or credential/model change is provided
by this slice. The kernel has no executable CLI and is not called by launchers.

`tools/wd_external_watcher_restarter.py` exposes two APIs:

- `evaluate(config, snapshot, now)`: pure, strict same-session decision.
- `run_cycle(config, now, services)`: one attempt through injected, reviewed
  adapter methods `observe`, `reserve`, `start`, `alarm`, optional `clock`.

Actual production adapters must provide a live clock. The fixed `now` fallback
is for deterministic inert tests, not an elapsed-time freshness measurement.
Process argv vectors must come from an actual Windows command-line parser,
not whitespace splitting or substring matching. Opaque relevant process
identity makes evidence unknown, never absent. Other exact agent watchers are
untouched. Only the external Tools producer form is accepted; a verified
native session producer or changed session blocks replacement.

The supplied authoritative producer record must be verified by the adapter
against current session evidence. `producer_record_verified: true` in an
arbitrary input file is **not** authorization. Config likewise needs an
external reviewed anchor; kernel input shape validation does not attest it.
No deployment anchor or additional capability is manufactured by this code.

An observation is accepted only when complete, at most 10 seconds old and not
future-dated. Expected owner and watcher bind PID, creation instant and exact
argv. Missing wake sentinel is ordinary idle state, not watcher death. A live
watcher with a dirty bit older than 300 seconds raises an unconsumed-sentinel
alarm, never kills or restarts that process. A verified missing external
watcher may request one start only while its same-generation owner remains
exactly live and no unaccounted Tools watcher exists.

Before starting, the adapter must atomically reserve and flush an immutable
intent. Existing intents (successful, failed, uncertain) are retained and
block repetition. The kernel then takes a second complete observation; any
change aborts. Returned new PID/creation/argv must match another fresh snapshot.
This proves only a process, not producer readiness, delivery or next-turn
readiness. A failed notice is explicitly reported; a returned alarm callback
is not called a durable bridge receipt.

Every result carries strict boolean `next_turn_ready: false`, separate
`readiness_state: unknown`, and `authority: none`. Never use the state string
as a readiness bit. The late c063 HIGH report exposed the earlier truthy
`"unknown"` value; behavioral RED6fail48pass precedes this boolean repair.
Full call-sequence and a missing post-start process assert the third observation.
Two additional executed source mutants cover truthy readiness and skipped
post-start verification. This repair does not complete any adapter sibling.

## Required follow-up before any installation

Implement and independently review a separate Limited scheduled-task adapter:

1. Reuse installed read-only process evidence and authoritative session
   producer records; no competing capacity collector or raw bridge log reader.
2. Verify the externally anchored package/config and every helper dependency.
3. Use one lock shared with **every launcher/replacement writer**. Two snapshots
   alone cannot eliminate a concurrent producer-start race. Scheduled Task
   `IgnoreNew` alone does not serialize launchers or manual invocations.
4. Preserve create-new, flushed intents and exact receipts across interruption;
   no automatic retry of uncertain start, deletion, truncation or PID adoption.
5. Launch only the pinned standalone Watch-Bridge argv, hidden and Limited.
   Verify actual startup readiness without mistaking PID existence for it.
6. Make alarms visible without an LLM, retaining durable sanitized evidence.
   An operator-visible delivery path needs its own reviewed acceptance tests.
7. RED-test adapter locks, source/record drift, start/receipt uncertainty and
   actual PS5/PS7 behavior. Keep the task disabled/unregistered until reviewed.

These adapter requirements are unfinished work, not inferred permission to
install or repair runtime during source development. Do not install a task
that calls the kernel directly or trusts arbitrary snapshot/config JSON.

## Local proof

Initial absent module: 30 setup errors. Conservative inert stub then produced
9 assertion failures / 21 passes (behavioral RED), before implementation.
The initial implementation checkpoint passed 30 tests. Additional adversarial
tests execute five named source mutants entirely in memory using inert
Services doubles: incomplete snapshot, PID creation, stale-live restart,
post-intent race and uncertain-start replay. No process is launched or killed.
Canonical lane audit logs record exact commands/results; none are a live
restart, actual alarm-delivery acceptance or full-suite claim.
