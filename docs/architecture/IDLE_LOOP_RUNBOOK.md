# Idle Loop Runbook

**Status:** companion runbook for `tools/idle_loop_once.py`
**Scope:** operator-approved idle-window scheduling and two-agent bridge loop
**Companion docs:** `IDLE_PROTOCOL_V1.md`, `IDLE_AUTONOMY_CHARTER.md`, `IDLE_CONSENSUS_ARTIFACT_V1.md`

## Purpose

This runbook defines how to run the idle loop without waiting for a manual
operator prompt. The scheduled command runs one bounded tick, exits, and lets
the bridge state decide the next tick. It is not a daemon, not a hidden model
loop, and not a bypass around the Idle Autonomy Charter.

The intended production cadence is every 30 minutes. Shorter intervals are
allowed only for local smoke testing because idle detection deliberately treats
recent substantive bridge traffic as active work.

## Invariants

- `idle_loop_once.py` is the only scheduled idle entrypoint.
- Each invocation runs once and exits.
- `--dry-run` is the default local validation mode.
- `--apply` may be scheduled only after the dry run is clean.
- The scheduler never edits the charter, bridge gate scripts, credentials, or
  protected operator policy files.
- Merge decisions still pass through the seven parallel charter conditions:
  consensus, CI green, receipt verified, rate limit, clean mergeability,
  allowlist match, and no denylist hit.
- If a stop condition is reached, the loop writes or preserves a bridge event
  and exits. It does not prompt the operator interactively.

## Preflight

Run these checks before installing any schedule:

```powershell
cd C:\Python\project2-master
$env:AGENT_BRIDGE_RUNTIME_ROOT = 'C:\Python\project2-master\.agent-bridge'

powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\.agent-bridge\bin\Get-AgentBridgeStatus.ps1 -MaxUnresolved 15

.\.venv\Scripts\python.exe tools\idle_loop_once.py --dry-run --json
```

Expected preflight shape:

- No active overlapping write claims.
- Claude and Codex have recent bridge liveness or heartbeat from their active
  interactive shells, when shells are expected to be online.
- `idle_loop_once.py --dry-run --json` exits cleanly.
- Any result of `operator_review_required`, `charter_violation`,
  `invalid_event`, or `low_quality` is treated as a stop condition, not as a
  reason to schedule `--apply`.

## Windows Task Scheduler

Install a 30-minute schedule from an elevated PowerShell session:

```powershell
$repo = 'C:\Python\project2-master'
$python = Join-Path $repo '.venv\Scripts\python.exe'
$taskName = 'WaggleDance Idle Loop Once'

$action = New-ScheduledTaskAction `
  -Execute $python `
  -Argument 'tools\idle_loop_once.py --apply --json' `
  -WorkingDirectory $repo

$trigger = New-ScheduledTaskTrigger `
  -Once `
  -At ((Get-Date).AddMinutes(5)) `
  -RepetitionInterval (New-TimeSpan -Minutes 30) `
  -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet `
  -MultipleInstances IgnoreNew `
  -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 20)

Register-ScheduledTask `
  -TaskName $taskName `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Description 'Runs one charter-gated WaggleDance idle loop tick every 30 minutes.'
```

Operational notes:

- `IgnoreNew` prevents two ticks from overlapping if a previous tick is still
  running.
- The task runs the repo-local virtualenv Python directly; it does not launch
  Claude Code or Codex CLI.
- Bridge runtime discovery should resolve to the repo `.agent-bridge` root. If
  the machine has a different runtime root, set `AGENT_BRIDGE_RUNTIME_ROOT` in
  the task environment or wrap the command in a small local launcher script.

Check the task:

```powershell
Get-ScheduledTask -TaskName 'WaggleDance Idle Loop Once'
Get-ScheduledTaskInfo -TaskName 'WaggleDance Idle Loop Once'
```

Disable without deleting:

```powershell
Disable-ScheduledTask -TaskName 'WaggleDance Idle Loop Once'
```

Emergency stop:

```powershell
Unregister-ScheduledTask -TaskName 'WaggleDance Idle Loop Once' -Confirm:$false
```

## Cron Equivalent

On Linux or WSL deployments, use cron with the same one-tick contract:

```cron
*/30 * * * * cd /srv/waggledance && ./.venv/bin/python tools/idle_loop_once.py --apply --json >> logs/idle_loop_once.log 2>&1
```

For systemd timer deployments, keep the service `Type=oneshot` and set
`Persistent=true` on the timer if missed ticks should run after boot. Do not
configure a long-running idle loop service.

## Result Handling

Each tick should produce a machine-readable JSON result. The scheduler does not
interpret it; the agents do through the bridge.

| Result | Action |
| --- | --- |
| `not_idle` | Exit. Existing work continues. |
| `no_instance_emit_round_1` | The tick may emit the first idle proposal only when the tool is run with `--apply` and idle predicates still hold. |
| `mid_protocol_waiting_for_peer` | Exit. The peer agent should answer the open idle-protocol event through the bridge. |
| `soft_convergence` or `hard_convergence` | Run the charter-gated consensus artifact, draft PR, and auto-merge chain. |
| `operator_review_required` | Stop automated progression and leave the evidence in bridge artifacts. |
| `charter_violation`, `invalid_event`, `low_quality` | Stop automated progression for that instance. |

## Agent Loop Contract

The scheduler only creates opportunities. Claude and Codex still own the work
selection loop:

1. Read the bridge before waiting.
2. If an incoming request is open, answer it.
3. If an active claim exists for the agent, continue it or release it.
4. If another agent owns a write claim, take a non-overlapping read-only scout
   or review task.
5. If no claims are active, claim the highest-value unblocked implementation,
   scout, or review task.
6. Use alternate review loops before merge: one agent implements, the other
   reviews, then roles swap on the next slice.
7. Escalate only for protected paths, credentials, destructive operations,
   external side effects, unresolved write-scope conflict, or explicit charter
   stop conditions.

The bridge helper for the next safe action is:

```powershell
$env:AGENT_BRIDGE_RUNTIME_ROOT = 'C:\Python\project2-master\.agent-bridge'
powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\.agent-bridge\bin\Get-BridgeNextAction.ps1 -Agent codex -Json
```

Claude uses the same command with `-Agent claude`.

## Recovery

If a scheduled tick fails:

1. Disable the schedule.
2. Run `Get-AgentBridgeStatus.ps1` and inspect active claims.
3. Release only stale claims through the bridge scripts; do not delete claim
   files manually.
4. Run `idle_loop_once.py --dry-run --json`.
5. Re-enable the schedule only after the dry run is clean.

If the agent CLI shells are down, restart them from interactive terminals. The
scheduled task is not a replacement for Codex or Claude Code sessions; it only
executes the one-tick idle substrate.
