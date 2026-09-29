# Grok repository read-only sessions

The installed `C:\Python\Invoke-WdGrok.ps1` exposes the packaged session controller:

```powershell
& C:\Python\Invoke-WdGrok.ps1 -ReadOnly -TaskId 'review/example' -PromptPath C:\Python\review.md -RepositoryPath C:\Python\project2 -Commit '<full 40-character commit SHA>'
```

`-ReadOnly` enables model-directed `read_file`, `list_dir` and literal `grep`
requests against that immutable Git commit through the bounded Git blob broker.
Git comes from the hash-verified fleet configuration, not PATH. Grok's native
tools remain denied. `-MaxRounds` is 2..8 (default 6); the existing consultation
budget, task exceptions, lifecycle events and lock still apply.

The shared hourly budget unit is one consultation, reserved once before any
model round starts. All 2..8 rounds belong to that same reservation. Failure,
timeout or interruption consumes it too; rounds do not reserve separate hours,
and neither retries nor a separate ledger are used. The global consultation
lock remains held for the complete session.

The helper's keyword-only `consult(..., timeout_seconds=300)` accepts only a
built-in integer from 1 through 2400 seconds, rejecting booleans and coercible
values before acquiring the lock or reserving budget. It forwards that value to
the runner. Text-only advisory mode keeps the 300-second default.

The controller integration must pass `max_rounds * 300` as the session timeout:
600..2400 seconds for 2..8 rounds, or 1800 seconds at the default six rounds.
Each model process must use `min(300, remaining_session_seconds)`, so no process
gets more than 300 seconds and the session deadline never exceeds 2400 seconds.
This helper interface alone does not establish that the separately owned
controller enforces these bounds; check its integrated implementation before
claiming that behavior.

Reservations record their actual `timeout_seconds`. A status read classifies a
still-`reserved` attempt past that deadline as `interrupted_or_unknown`; older
reservations with no recorded timeout use the conservative 2400-second bound.
The response retains `recorded_status` and the complete `raw_state`. It never
rewrites the ledger, refunds the reservation or kills a process. Such an unknown
attempt reports `eligible=false` even after the hour passes; the separate
`hourly_budget_eligible` field describes only the clock-based budget boundary,
not readiness. Resolve the durable unknown attempt through an explicitly
authorized reconciliation rather than treating elapsed time as completion.
Neither a task exception nor an elapsed hour reconciles an unfinished attempt:
consultation defers with `deferred_unreconciled_attempt` while the durable state
is still `reserved`, before or after its deadline, without overwriting that
reservation or consuming an exception attempt. An explicit reconciliation
adapter is not included; no process-exit or readiness guarantee is implied.
Deferred consultation responses have `status=deferred`,
`consultation_attempted=false`, and a null new `request_id`. The prior attempt
is kept under `previous_attempt`, never relabelled as the new caller's task.
Both consultation entrypoints exit 2 for a deferral (0 only for an answered
consultation, 1 for a failed attempt); `-Status` remains a read-only observation.

Unfinished attempts have no next-eligible time. While the durable state is
`reserved` (before or after its deadline), both `-Status` and a deferral report
`next_eligible_utc: null`: neither the clock nor an exception reconciles the
attempt. The clock cooldown stays visible, separately, as
`hourly_budget_next_eligible_utc` (with `hourly_budget_eligible`). An
hourly-limit deferral keeps the clock value in `next_eligible_utc`.

Deferral observations (contract). A deferral reserves nothing, so it never mints
a consultation `request_id`. The response and its `deferred` lifecycle event
carry the same fresh 32-hex `observation_id`. The bridge event payload
(`wd.grok-consultation-event.v1`) then has `consultation_id: null` and
`observation_id`, and its session and run are `grok-deferral-<observation_id>`.
The `started`, `answered` and `failed` stages keep `consultation_id` = the
reservation `request_id` and session `grok-consult-<request_id>`. The lifecycle
writer refuses a deferral that names a `request_id` and a consultation stage
without one.

Exit codes of `Invoke-WdGrok.ps1` are the Python tool's: 0 for an answered
consultation (or a successful `-Status`/`-Inventory`), 1 for a failed attempt,
and 2 for a deferral or a blocked input. Tell deferral and block apart by the
JSON: a deferral has `status: "deferred"` and a `decision`; a block has
`status: "blocked"` and an `error`. A deferral never presents the earlier
attempt's answer as its own: that attempt is nested under `previous_attempt`,
and the exit stays 2. Caller semantics, deliberately:
- A run where this script is the process's own `-File` target
  (`powershell -File Invoke-WdGrok.ps1 ...`) exits with that code. Before this
  change, `-File` reported 0 even after a failed consultation.
- A PowerShell caller that runs the script with `&` from another script, or
  dot-sources it, keeps the `Invoke-WdBridgePython.ps1` convention. There is no
  `exit` (it would abandon the caller's output capture); the code is in
  `$LASTEXITCODE`, and output capture is unchanged.
- A wrapper that ends without publishing a code is reported as 1.
- `-Command` callers use `& '<Invoke-WdGrok.ps1>' ...; exit $LASTEXITCODE`.

For long consultations, keep the caller window and its command wait alive long
enough for the configured session (up to 2400 seconds) plus completion logging.
A detached launch with durable stdout/stderr logs and a recorded process
identity is a recommended caller arrangement when a terminal wait is too short;
this document does not launch it. Caller waiting must not introduce a free
wrapper timeout, retry, additional consultation or budget bypass.

No arguments (or `-Status`) checks the old helper status without a model call.
`-Inventory` alone inventories inherited hooks/MCP/LSP without a model call.
`-PromptPath` without `-ReadOnly` retains the existing text-only advisory mode.
Reboot/startup never automatically initiates a Grok consultation.

The controller is NOT an OS-level read-only sandbox. If inherited executable
components exist, it refuses before consultation unless their exact inventory
digest is deliberately supplied as `-AcknowledgeInheritedSurface`. This is an
acknowledgement of possible inherited execution, not an isolation guarantee;
the wrapper never supplies it automatically. Unreadable inventory fails closed.

Release integration was requested without additional tests or model trials.
The controller's multi-round CLI behavior is not runtime-validated. Installation
and inclusion in the bundle do not establish successful Grok execution.
