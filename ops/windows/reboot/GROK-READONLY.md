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
