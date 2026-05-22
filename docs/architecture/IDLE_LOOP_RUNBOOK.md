# Idle Loop Runbook

**Status:** companion runbook for `tools/idle_loop_once.py`
**Scope:** operator-approved idle-window scheduling and two-agent bridge loop
**Companion docs:** `IDLE_PROTOCOL_V1.md`, `IDLE_AUTONOMY_CHARTER.md`, `IDLE_CONSENSUS_ARTIFACT_V1.md`

## Purpose

This runbook defines how to run the idle loop without waiting for a manual
operator prompt. The scheduled command runs one bounded read-only tick, exits,
and lets the bridge state decide the next agent action. It is not a daemon, not
a hidden model loop, and not a bypass around the Idle Autonomy Charter.

The intended production cadence is every 30 minutes. Shorter intervals are
allowed only for local smoke testing because idle detection deliberately treats
recent substantive bridge traffic as active work.

## WD Mission Boundary

The idle and dream loops exist only to help the large development models keep
improving WaggleDance and its project domains without waiting for the operator
to manually say "continue." They are not a general-purpose autonomous browsing,
research, or task-execution loop.

Valid idle/dream work must produce or refine at least one WD artifact:
architecture, code, tests, security hardening, threat models, reliability
evidence, competitor-informed product gaps, documented risks, or concrete
backlog candidates. Competitor monitoring is allowed only as evidence for WD
strategy and implementation decisions. Security stewardship is allowed only for
WD-owned code, dependencies, configuration, secrets handling, bridge/runtime
protocols, and defensive analysis. The loop must not probe, attack, scrape,
or automate against third-party systems unless a separate operator-approved
tooling path explicitly authorizes that external side effect.

## Invariants

- `idle_loop_once.py` is the only scheduled idle entrypoint.
- Each invocation runs once and exits.
- The tool is read-only and has no `--apply` mode.
- Follow-up bridge writes, payload generation, PR drafting, and auto-merge
  attempts remain owned by the existing purpose-built tools and the live agents.
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

.\.venv\Scripts\python.exe tools\idle_loop_once.py --json
```

Expected preflight shape:

- No active overlapping write claims.
- Claude and Codex have recent bridge liveness or heartbeat from their active
  interactive shells, when shells are expected to be online.
- `idle_loop_once.py --json` exits cleanly.
- Any result of `operator_review_required`, `charter_violation`,
  `invalid_event`, or `low_quality` is treated as a stop condition, not as a
  reason for the scheduler to run any follow-up mutating command.

## Windows Task Scheduler

Install a 30-minute schedule from an elevated PowerShell session:

```powershell
$repo = 'C:\Python\project2-master'
$python = Join-Path $repo '.venv\Scripts\python.exe'
$taskName = 'WaggleDance Idle Loop Once'

$action = New-ScheduledTaskAction `
  -Execute $python `
  -Argument 'tools\idle_loop_once.py --json' `
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
  -Description 'Runs one read-only WaggleDance idle loop status tick every 30 minutes.'
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
*/30 * * * * cd /srv/waggledance && ./.venv/bin/python tools/idle_loop_once.py --json >> logs/idle_loop_once.log 2>&1
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
| `no_session` | Exit after reporting the recommended `run_idle_protocol_once.py --emit --json` command. A live agent must decide whether to run it. |
| `mid_protocol` | Exit. The peer agent should compose and emit the next idle-protocol payload through the existing activation tool. |
| `convergence_reached` | Exit after reporting the implementer-chain route. A live agent converts consensus into a candidate diff before artifact, draft PR, status snapshot, and auto-merge tools can run. |
| `stale_terminal_session` | Exit after reporting `agent_next_task.py`. The old terminal idle-protocol instance remains recorded, but it no longer blocks new safe work selection after the configured stale window. |
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
7. Keep every scout or dream output tied to WD advantage: a repo change,
   test gap, security risk, competitor-informed design note, or backlog item.
8. Escalate only for protected paths, credentials, destructive operations,
   external side effects, unresolved write-scope conflict, or explicit charter
   stop conditions.

The bridge helper for the next safe action is:

```powershell
$env:AGENT_BRIDGE_RUNTIME_ROOT = 'C:\Python\project2-master\.agent-bridge'
powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\.agent-bridge\bin\Get-BridgeNextAction.ps1 -Agent codex -Json
```

Claude uses the same command with `-Agent claude`.

## Self-pacing tick (`tools/bridge_loop_tick.py`)

For a session that self-paces via wakeups (no fixed external cron), the FIRST
action of every tick is one read-only aggregator call that drains the inbox,
detects already-approved merges, surfaces operator decision packs, detects
heartbeat-only peer sessions, and recommends the next wakeup interval:

```text
python tools/bridge_loop_tick.py --agent claude --check-prs --repo OWNER/NAME --json
```

Self-wakeup harnesses that are allowed to keep the peer active pass
`--emit-peer-activation`; without that flag the tick only reports the proposed
handoff.

By default it chains the existing primitives (read-only; no `--apply`, never
runs `gh pr merge`) and reports an ordered worklist:

1. **Drain inbox** — `next_action` from `recommend_next_action`; if
   `answer_incoming`, handle the peer RCO request / handoff first.
2. **Complete approved merges** — each entry in `merge_ready` with `ready:true`
   is a PR this agent rco_pass'd that is now CI-green + mergeable clean +
   preflight-clear + head-matched. Complete it in the SAME tick via the existing
   gated flow, then record the close so the next tick does not re-detect it:
   ```text
   python tools/pr_status_snapshot.py <pr> --out snap.json
   gh pr merge <pr> --squash --match-head-commit=<approved_head>   # Rule 9 peer-RCO
   .\.agent-bridge\bin\Write-AgentEvent.ps1 -Agent <me> -Type done \
       -TaskId <task> -Status merged -PayloadJson '{"pr":<pr>}'
   ```
   (Idle-consensus-protocol PRs instead go through
   `idle_consensus_auto_merge.py --apply`, which adds the consensus + MAGMA
   receipt + 5/day gates. `bridge_loop_tick` covers the direct peer-RCO path.)
3. **Surface operator packs** — each `open_operator_packs` entry is a
   charter-gated decision needing a one-step operator sign-off
   (`docs/operator_inbox/<id>.yaml`, schema `OPERATOR_DECISION_PACK_V1.md`).
   Emit `type=decision status=operator_signoff_requested to=operator` once per
   new pack. The loop NEVER resolves a pack.
4. **Activate the peer** — when the other agent is only heartbeating and has no
   recent substantive bridge event or active claim, `--emit-peer-activation`
   writes the recommended `peer_activation.bridge_event` as
   `type=handoff status=scout_requested`. The peer should take an unblocked
   read-only scout/review/simulation slice while operator-gated packs remain
   fail-closed.
5. **Adaptive wakeup** — schedule the next wakeup from
   `recommended_wakeup_seconds`: ~90s when there is actionable merge/RCO work,
   peer activation is needed, or unblocked work can be claimed; ~240s when CI
   is in flight or a claim is active; ~1800s only when quiet (respecting the
   ~5-minute prompt-cache TTL). Open operator packs remain fail-closed but do
   not lengthen the wakeup while unrelated unblocked work is available.

This removes the need for a human "continue" poke between ticks and lets an
RCO-passed PR merge in the same tick it becomes ready — while every mutation
still flows through the existing charter-gated tools and escalation categories
stay operator-gated.

Peer activation is not an automatic mutation path. It is a bridge handoff that
keeps the second agent busy on evidence, tests, competitor analysis, simulation,
or next-PR scoping whenever it would otherwise only heartbeat and wait for the
operator.

## Recovery

If a scheduled tick fails:

1. Disable the schedule.
2. Run `Get-AgentBridgeStatus.ps1` and inspect active claims.
3. Release only stale claims through the bridge scripts; do not delete claim
   files manually.
4. Run `idle_loop_once.py --json`.
5. Re-enable the schedule only after the read-only tick exits cleanly.

If the agent CLI shells are down, restart them from interactive terminals. The
scheduled task is not a replacement for Codex or Claude Code sessions; it only
executes the one-tick idle substrate.
