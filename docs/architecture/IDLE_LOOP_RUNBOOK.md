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

### Bridge Wake Consumer

`Watch-Bridge.ps1` and `Monitor-AgentBridge.ps1` do not execute agent work.
The watcher only creates `wake_<agent>` sentinels and the monitor only tails
bridge events. A live Codex lane that should react to wake requests must run the
consumer loop:

```powershell
$env:AGENT_BRIDGE_RUNTIME_ROOT = 'C:\Python\project2-master\.agent-bridge'
$env:AGENT_BRIDGE_AGENT_UUID = '7a8af68d-20bc-4598-9953-23c5dd98b102'
$env:AGENT_BRIDGE_ROLE = 'tools-tests'
$env:AGENT_BRIDGE_CAPABILITIES = 'tools,tests,bridge_loop,rival_checks,docs,bridge_event,work_queue'

$dir = (
  Get-ChildItem 'C:\Python\waggledance-agent-worktrees' -Directory -Filter 'codex-tools-1-*session*' |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
).FullName

powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\.agent-bridge\bin\Start-AgentBridgeConsumerLoop.ps1 `
  -Agent codex-tools-1 `
  -AgentUuid $env:AGENT_BRIDGE_AGENT_UUID `
  -Role $env:AGENT_BRIDGE_ROLE `
  -Capabilities $env:AGENT_BRIDGE_CAPABILITIES `
  -Worktree $dir `
  -Forever `
  -PollSeconds 60
```

By default the consumer also runs a bounded bridge tick on every poll, even
when no wake file exists, so open work can continue moving. Add `-WakeOnly`
only for diagnostics that must execute strictly on targeted wake events. Use
`-DryRun -MaxIterations 1` to verify the resolved worktree and Codex argument
array without invoking the model. The consumer intentionally omits `--model`
unless `-Model` or `AGENT_BRIDGE_CODEX_MODEL` is set, so it follows the current
Codex config default instead of pinning a stale limited model. On Windows the
consumer resolves `codex.cmd` before the PowerShell `codex.ps1` shim, because
the shim exits the host process unhelpfully inside long-running automation.
Real Codex ticks start `Start-BridgeHeartbeat.ps1` for the tick duration, so a
claim opened inside `codex exec` does not stale-release during long tests.
Each tick is still bounded by `-CodexTimeoutSeconds` (default 600). A timed-out
tick is terminated, logs exit code `124`, and the next poll can try again or
surface bridge evidence instead of letting one long `codex exec` block wake
delivery indefinitely. Real Codex ticks also emit bridge `status` events at
start and terminal finish/failure/timeout with the log path and timeout
metadata, so liveness scouts can distinguish an active tick from a stuck
consumer wrapper.
The source defaults are conservative: `-Sandbox workspace-write` and
`-ApprovalPolicy on-request`. Trusted operator-run loops that need cross-repo
bridge writes must pass broader authority explicitly, for example
`-Sandbox danger-full-access -ApprovalPolicy never`.

## Self-pacing tick (`tools/bridge_loop_tick.py`)

For a session that self-paces via wakeups (no fixed external cron), the FIRST
action of every tick is one read-only aggregator call that drains the inbox,
detects already-approved merges, surfaces operator decision packs, detects
heartbeat-only peer sessions, and recommends the next wakeup interval:

```text
python tools/bridge_loop_tick.py --agent claude --check-prs --repo OWNER/NAME --expected-base-sha <fresh-current-main-sha> --json
```

Self-wakeup harnesses that are allowed to keep the peer active pass
`--emit-peer-activation`; without that flag the tick only reports the proposed
handoff.

By default it chains the existing primitives (read-only; no `--apply`, never
runs a merge command) and reports an ordered worklist:

1. **Drain inbox** — `next_action` from `recommend_next_action`; if
   `answer_incoming`, handle the peer RCO request / handoff first.
2. **Complete approved merges** — each entry in `merge_ready` with `ready:true`
   is a PR this agent rco_pass'd that is now CI-green + mergeable clean +
   preflight-clear + head-matched + full bridge consensus verified. Complete it
   only through the fail-closed bridge merge-driver, which rechecks
   `check_rco_pass_present`, `verify_bridge_consensus`, and
   `check_bridge_changes_requested` against the canonical branch task id before
   running `gh pr merge`. Do not run direct `gh pr merge` from a lead or peer
   shell. The driver records the close so the next tick does not re-detect it:
   ```text
   powershell -NoProfile -ExecutionPolicy Bypass -File C:\Python\Invoke-BridgeMergeDriver.ps1 -Apply -MaxMergesPerRun 1
   ```
   (Idle-consensus-protocol PRs instead go through
   `idle_consensus_auto_merge.py --apply --require-bridge-consensus
   --expected-base-sha <fresh-current-main-sha>`, which adds the consensus +
   MAGMA receipt + 5/day gates. `bridge_loop_tick` is a read-only detector; it
   does not authorize direct peer-RCO merges.)
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
   or peer activation is needed; ~240s when CI is in flight, a claim is active,
   **the peer holds an active PR-producing claim** (see "Self-merge timeout
   window" below), or open operator packs coexist with otherwise claimable
   unblocked work; ~1800s when genuinely quiet (respecting the ~5-minute
   prompt-cache TTL). Open operator packs remain fail-closed but no longer
   force the longest wakeup while unrelated unblocked work may be available.

### RCO wakeup window

When one agent (typically Codex in this repo's bridge loop) opens a PR and CI
goes green, the peer-RCO leg remains fail-closed. The producing agent's harness
must not self-merge without an explicit head-bound `claude-rco-1` `RCO_PASS`
or an explicit operator override. If no `RCO_PASS` arrives before the wakeup
window expires, the correct automated result is `operator_review_required`
with bridge evidence for the operator; silence never default-allows.

The MAGMA sprint baseline's
`claude_activation_contract.rco_timeout_minutes_after_ci_green` field
(`docs/runs/magma_100h_sprint_2026_05_23/baseline.json`) is retained only as a
peer-wakeup budget. It is not merge authority. Consequence for the peer (here,
Claude) loop discipline: the heartbeat must already be cache-warm
(`<=WAKEUP_IN_FLIGHT`, 240s) **before** the PR opens on GitHub, not just once
the PR is visible. The code-side enforcer is
`tools/bridge_loop_tick.py::peer_has_active_pr_producing_claim`: it scans
**backwards** for the peer's latest event with `(type, status)` in
`PEER_PR_PRODUCING_SIGNALS` — `(claim, active)`, `(claim, started)`,
`(status, active)`, or `(handoff, active_requested)` — within
`PEER_ACTIVE_CLAIM_MAX_AGE_MINUTES` (default 15 min). The claim stays
`active=True` even when the peer has emitted later non-closing substantive
events (decision/clarification/finding/message) on the same or other tasks.
It is cleared only when a strictly LATER peer event for the SAME `task_id`
is terminal: `type=done` (any status) or `status` in
`PEER_TERMINAL_STATUSES` = `{blocked, abandoned, released}`.
`_recommended_wakeup` consumes that signal and returns `WAKEUP_IN_FLIGHT`
so the next tick can catch the imminent PR with time to RCO before the merge
gate would otherwise have to stop at `operator_review_required`.

This codifies the lesson from PRs #584 and #585 (2026-05-22), where a
peer-side 1200s and then 1800s heartbeat skipped over Codex's RCO requests
and both PRs progressed without Claude's review. The window is short enough
that a long heartbeat **across** an active peer claim is too long, but narrow
enough that a 240s heartbeat reliably catches the RCO request and prevents an
operator escalation caused only by peer silence.

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
