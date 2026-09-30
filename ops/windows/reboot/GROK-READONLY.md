# Grok repository read-only sessions

The installed `C:\Python\Invoke-WdGrok.ps1` exposes the packaged session controller:

```powershell
& C:\Python\Invoke-WdGrok.ps1 -ReadOnly -TaskId 'review/example' -PromptPath C:\Python\review.md -RepositoryPath C:\Python\project2 -Commit '<full 40-character commit SHA>'
```

`-ReadOnly` enables model-directed `read_file`, `list_dir` and literal `grep`
requests against that immutable Git commit through the bounded Git blob broker.
Git comes from the hash-verified fleet configuration, not PATH. Grok's native
tools remain denied. `-MaxRounds` is 2..8 (default 6); the helper's single-flight
reservation, unfinished-attempt refusal, lifecycle events and lock still apply.

There is no local hourly or weekly quota (direct operator direction, 2026-09-30).
One consultation is reserved once, durably, before any model round starts; all
2..8 rounds belong to it. An answer, a failure or a timeout completes it, and
the next consultation may start at once: no cooldown, no local budget and no
automatic retry (each consultation is one explicit call). Grok's own provider
limits are real but not readable headless, so `-Status` reports
`provider_quota: "unknown"` separately from the local `local_availability`.
`provider_evidence` is the last attempt's own recorded failure, verbatim; it may
be local, and it is never read as a quota, a reset time or a reason to retry.
Task exception grants waived the removed hourly budget and are retired: a
consultation that presents one is refused before the lock, and grants already
recorded in the state file are carried forward as history only. The global
consultation lock remains held for the complete session.

The helper's keyword-only `consult(..., timeout_seconds=300)` accepts only a
built-in integer from 1 through 2400 seconds, rejecting booleans and coercible
values before acquiring the lock or reserving an attempt. It forwards that value to
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
rewrites the state file or kills a process. Such an unknown attempt reports
`eligible=false` and `local_availability: "unreconciled_attempt"` however much
time passes. Resolve the durable unknown attempt through an explicitly
authorized reconciliation rather than treating elapsed time as completion:
consultation defers with `deferred_unreconciled_attempt` while the durable state
is still `reserved`, before or after its deadline, without overwriting that
reservation. An explicit reconciliation adapter is not included; no
process-exit or readiness guarantee is implied.
Deferred consultation responses have `status=deferred`,
`consultation_attempted=false`, and a null new `request_id`. The prior attempt
is kept under `previous_attempt`, never relabelled as the new caller's task.
Both consultation entrypoints exit 2 for a deferral (0 only for an answered
consultation, 1 for a failed attempt); `-Status` remains a read-only observation.

Local availability (`local_availability`; `eligible` describes it, never the
provider's quota): `available` after a completed attempt (`eligible=true`,
`next_eligible_utc: null`); `unreconciled_attempt` while the durable state is
`reserved`, before or after its deadline (`eligible=false`,
`next_eligible_utc: null`: only an explicit reconciliation resolves it);
`clock_regressed` when the clock reads earlier than the recorded attempt
(`eligible=false`, `next_eligible_utc` = that recorded time), and a consultation
then defers with `deferred_clock_regression`. The removed hourly fields
(`hourly_budget_*`, `deferred_hourly_limit`) are no longer produced.

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
- SUPPORTED: a run where this script is the process's own explicit, top-level
  `-File` target (`powershell -File Invoke-WdGrok.ps1 ...` or
  `pwsh -File Invoke-WdGrok.ps1 ...`) exits with that code. Before this change,
  `-File` reported 0 even after a failed consultation. The script detects this
  case from the process command line. The first `-File` token (also `-f`, `-fi`,
  `-fil` or a `/` form) must be followed by this script's own path; the paths
  are compared in full and case-insensitively.
- UNVERIFIED, not a runtime guarantee: a positional invocation with no `-File`
  token, such as `pwsh Invoke-WdGrok.ps1 ...` (pwsh treats the first positional
  argument as the file) or `powershell Invoke-WdGrok.ps1 ...` (Windows
  PowerShell treats it as a command). Other hosts and unusual command lines are
  also unverified. No `-File` token names this script, so it does not `exit`,
  and the process exit code is whatever the host reports: it may be 0 after a
  failed or deferred consultation. Do not rely on the process exit code in these
  forms. Use the explicit `-File` form, or read `$LASTEXITCODE` and the JSON
  `status`.
- A PowerShell caller that runs the script with `&` from another script, or
  dot-sources it, keeps the `Invoke-WdBridgePython.ps1` convention. This also
  holds when that other script is itself the process's `-File` target. There is
  no `exit` (it would abandon the caller's output capture), the code is in
  `$LASTEXITCODE`, and output capture is unchanged. A dot-source runs in the
  caller's scope, so the script's `$ErrorActionPreference = 'Stop'` and its
  local variables stay in that scope.
- A wrapper that ends without publishing a code is reported as 1: the script
  sets `$LASTEXITCODE` to 1 before it calls the Python wrapper.
- `-Command` callers use `& '<Invoke-WdGrok.ps1>' ...; exit $LASTEXITCODE`.

The fixtures for these forms (a top-level `-File`, an in-process `&` capture, a
dot-source, a distinct outer `-File` script calling with `&` or `.`, and a
wrapper that publishes no code) use an isolated copy of this script with a stub
Python wrapper. They are authored but have NOT been run. The positional forms
have no fixture and stay unverified.

For long consultations, keep the caller window and its command wait alive long
enough for the configured session (up to 2400 seconds) plus completion logging.
A detached launch with durable stdout/stderr logs and a recorded process
identity is a recommended caller arrangement when a terminal wait is too short;
this document does not launch it. Caller waiting must not introduce a free
wrapper timeout, retry, additional consultation or lock bypass.

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
