# Agent Bridge Protocol

Purpose: let Claude and Codex coordinate without the operator relaying
"done", "continue", or "who owns this file" messages.

This is a runtime bridge, not the source of truth. It lives under
`.agent-bridge/` and is safe to clear between sessions.

### Reboot bootstrap

If the machine restarts or new Claude/Codex PowerShell windows are opened,
start from [`BOOTSTRAP.md`](./BOOTSTRAP.md). That file is the versioned
runbook for restoring the shared runtime root, launching each agent shell, and
resuming the alternating bridge/orchestrator loop without relying on terminal
history or operator paste-relay.

### Runtime root override

By default the bridge resolves its state directories
(`shared/`, `work_queue/`, `outbox/`, `inbox/`) under the same
`.agent-bridge/` directory the scripts live in. That works for the
default single-worktree layout (`C:\Python\project2-master`).

For per-agent-worktree setups (R23.2 default for parallel write work), set
`AGENT_BRIDGE_RUNTIME_ROOT` to a shared path that all agent worktrees
can reach. Example:

```powershell
# operator setup (once):
mkdir C:\Python\project2-master\.agent-bridge\shared
mkdir C:\Python\project2-master\.agent-bridge\work_queue
mkdir C:\Python\project2-master\.agent-bridge\outbox
mkdir C:\Python\project2-master\.agent-bridge\inbox

# per-agent shell (Claude):
$env:AGENT_BRIDGE_RUNTIME_ROOT = 'C:\Python\project2-master\.agent-bridge'

# per-agent shell (Codex):
$env:AGENT_BRIDGE_RUNTIME_ROOT = 'C:\Python\project2-master\.agent-bridge'
```

Alternative: junctions instead of env var. From each agent worktree
(e.g. `C:\Python\project2-claude`):

```cmd
:: replace per-worktree state with a link to the shared root
rmdir /s /q .agent-bridge\shared
mklink /j .agent-bridge\shared C:\Python\project2-master\.agent-bridge\shared
:: repeat for work_queue / outbox / inbox
```

When `AGENT_BRIDGE_RUNTIME_ROOT` is **set**, the scripts use it
unconditionally — they create the root directory if it doesn't
exist (first-run bootstrap) and fail loudly on malformed paths.
There is **no silent fallback** to per-worktree state when the env
var is set, because that would split-brain the agents on
first-run / typo / new-root paths. The fallback to per-worktree
state happens ONLY when the env var is **unset**.

To verify the redirect works on your setup before relying on it:

```powershell
.\.agent-bridge\bin\Test-BridgeRuntimeRootSmoke.ps1
```

The smoke test creates a fresh non-existing temp dir, points the
env var there, exercises Write/Read/Claim/Release/Status, and
verifies state lands under the temp dir (NOT under the worktree).
10/10 pass on a healthy bridge.

### Dedicated worktrees (R23.2)

Wake files and heartbeat events make the bridge responsive, but they do not
physically isolate a shared git working directory. R23.2 makes the structural
fix scriptable: create a dedicated worktree before starting a write-capable
agent session.

```powershell
cd C:\Python\project2-master
git fetch origin main
$wt = & .\.agent-bridge\bin\New-AgentBridgeWorktree.ps1 `
  -Agent codex `
  -TaskId "r22.1a-hotpath-benchmark" `
  -Base origin/main
cd $wt.worktree_path
. .\.agent-bridge\bin\Start-AgentBridgeSession.ps1 -Agent codex -RequireDedicatedWorktree
```

Use `-Agent claude` for Claude. Both worktrees point to the same bridge
runtime root, so claims and events remain shared while branch state is
separate. `Start-AgentBridgeSession.ps1 -RequireDedicatedWorktree` refuses to
bootstrap from the primary shared repo (`C:\Python\project2-master`) and is
the safest default for autonomous write loops.

Optional role metadata is supported for multi-instance runs. Keep `-Agent`
as the stable human-readable process id (`codex-impl-1`,
`claude-rco-scout`) and use `-Role`, `-AgentUuid`, and `-Capabilities` for
audit metadata:

```powershell
. .\.agent-bridge\bin\Start-AgentBridgeWorktreeSession.ps1 `
  -Agent codex-impl-1 `
  -Role impl `
  -AgentUuid 11111111-2222-3333-4444-555555555555 `
  -Capabilities bridge_event,work_queue
```

The C14 metadata path is declarative only: bridge readers display it and
events/claims preserve it, but role-based scheduling and lease enforcement
remain follow-up work. Existing `codex`/`claude` sessions without these
parameters remain valid.

Verify the substrate with:

```powershell
.\.agent-bridge\bin\Test-BridgeWorktreeIsolationSmoke.ps1
```

The smoke creates a temporary local repo, then proves codex and claude get
distinct worktrees/branches while the source repo branch remains unchanged.

### Stale-claim lease (R15)

Claim records carry a `last_heartbeat_utc` field that is bumped
by `Send-Liveness.ps1` on every `liveness/active` and
`heartbeat/active` event for the claim's owning agent. If
`now` is at or past the claim's effective lease expiry, the claim
is automatically archived to
`work_queue/done/<task>.<utc>.stale_lease.json` and a
`release/stale_lease` event is emitted by the `system` agent.

The default lease threshold is 300s, overridden by
`AGENT_BRIDGE_STALE_LEASE_SECONDS` or by passing `-StaleSeconds` to
`Invoke-StaleClaimSweep.ps1`. New claim records also carry
per-claim lease fields. A positive `lease_seconds` value replaces
the global threshold for that claim, and a later
`claim_lease_expires_utc` extends the effective expiry. Legacy
claims without those fields still use the global threshold.

The sweep is opportunistic: every call to
`Read-AgentBridge.ps1` and `Get-AgentBridgeStatus.ps1` runs
`Invoke-StaleClaimSweep.ps1 -Quiet` first, so any agent that
reads the bridge clears stale claims for everyone. The sweep
is a no-op when no claims are stale.

`operator` and `system` claims are **immune** from auto-release
even when stale — those are privileged claims that may
legitimately outlive the lease.

To verify the sweep works on your setup:

```powershell
.\.agent-bridge\bin\Test-BridgeStaleLeaseSmoke.ps1
```

13/13 pass on a healthy bridge. Covers stale auto-release,
fresh-claim-not-swept, heartbeat-extends-lease, operator/system
immunity, per-claim lease fields, and the env-var threshold
contract.

R23.1 adds a session heartbeat job so long-running active turns do
not accidentally let claims expire while tests or model reasoning run.
`Start-AgentBridgeSession.ps1` launches `Start-BridgeHeartbeat.ps1`
unless `$env:WAGGLE_BRIDGE_HEARTBEAT_ENABLED=0` or
`-SkipHeartbeatJob` is passed. The job emits `heartbeat/active`
every 60 s, which updates `last_heartbeat_utc` on the agent's active
claims. A heartbeat is a claim-lease keepalive, not proof that the
model loop is reading bridge events. If the agent has no active claim,
the heartbeat helper skips emission and exits after a bounded number of
idle iterations so orphaned helpers cannot make a stopped agent look
alive indefinitely.

See
[`iterations/codex_scout_tasks/r13_decision_record_2026_05_09.md`](../iterations/codex_scout_tasks/r13_decision_record_2026_05_09.md)
for the full R13 design notes and the deferred follow-ups
(per-agent worktrees, `Invoke-BridgeGit` allow-list expansion,
operation lock / lease for TOCTOU).

## Core Rules

1. Read the bridge before you wait.
   - If you are about to stop because you need the other agent, first run
     `Read-AgentBridge.ps1` and check whether the other agent already
     published `done`, `handoff`, `blocked`, or `finding` events.
   - Always pass your own `-Agent` name. The bridge reader prints a
     continuity section that marks incoming requests to you as `OPEN`
     or `answered`, and outgoing requests you sent as `WAITING-FOR-*`
     or `answered-by-*`, based on matching `task_id` values.
   - A normal read automatically emits `message/received` for each
     latest incoming request-like event. This proves "seen by the
     receiving agent" without pretending the request is complete.
     Use `-NoAckReceived` only for audits that must not mutate bridge
     runtime state.

2. Claim write work before editing.
   - A write task must have an active claim with `write_scope`.
   - Do not edit a path covered by another active write claim.
   - Read-only review can use `-Mode read-only` and does not block writers.
   - The Git branch is shared workspace state. Do not switch branches,
     rebase, merge, or otherwise move the worktree while another agent has
     an active write claim unless the other agent has released/handoffed the
     claim or you are working in a separate worktree. New claims record the
     current `git_branch` so status output can expose branch drift.
   - **Branch-moving git operations MUST go through `Invoke-BridgeGit.ps1`**
     during autonomous bridge-loop work. Raw `git switch / checkout / merge
     / rebase / pull` is forbidden when other agents may hold active claims.
     The wrapper enforces the same-agent + matching-cwd rule and blocks
     unsafe operations with exit 2; pass-through verbs (status, log, diff,
     add, commit, push, ...) run unchanged. `-Force` is restricted to
     operator/system; Claude/Codex agents may NOT bypass the guard.
   - **Separate git worktrees are the preferred model for real parallel
     implementation.** Use `New-AgentBridgeWorktree.ps1` so each agent's
     working tree is independent and a claim's branch state cannot be moved
     out from under another agent. Use raw `git worktree add` only for manual
     recovery/debugging.
   - **Known limits of the `Invoke-BridgeGit.ps1` wrapper** (Codex review
     2026-05-09): the wrapper is the immediate mitigation, not the
     structural fix.
     - **Allow-list, not deny-list, is the right shape long-term.** The
       wrapper only guards `switch / checkout / merge / rebase / pull`.
       `reset --hard`, `clean -fdx`, `stash`, `restore --source`,
       `cherry-pick / revert` (with conflicts), and submodule operations
       can also mutate shared workspace state. Treat raw destructive git
       as forbidden during bridge-loop work; extend wrapper scope as
       follow-up.
     - **TOCTOU window** between `Get-ActiveClaims` and `& git` exists.
       A genuine race needs an operation lock or lease, not just a
       check-then-act guard.
     - **Wrapper is not a hard sandbox.** Raw `git.exe` bypasses it.
       The PATH-shim or per-agent worktree approach is the only
       enforcement.
     - The wrapper is sufficient for the autonomous bridge-loop *as a
       cooperative protocol* — it makes the unsafe path noisy and
       audit-traceable. It is NOT sufficient against an adversarial or
       buggy agent that calls `git.exe` directly. Real parallel
       implementation should use separate worktrees.

3. Publish state after every meaningful step.
   - Use `status` for "I am working on X".
   - Use `finding` for review findings.
   - Use `test` for test outcomes.
   - Use `blocked` when you need a specific decision or permission.
   - Use `handoff` when the other agent should continue.
   - Use `done` when your current claimed task is complete.
   - When replying to a request, use the request's exact `task_id`.
     Put new labels in the message or paths, not in a new task id. This
     is required so monitors can prove the request was answered without
     operator relay.

4. Do not idle silently.
   - If the other agent owns the only conflicting write scope, either do
     read-only review, take an unclaimed task, or publish `blocked` with
     the exact blocker.
   - If there is no conflicting write scope and no blocking operator-only
     decision, claim a useful scout, review, verification, or implementation
     task. Do not wait for the operator to say "continue".

5. Operator escalation is only for external permissions, destructive
   actions, or unresolved claim conflicts.

6. Opinions require replies.
   - Any `finding/open`, `message/proposal`, `decision/proposal`,
     `blocked/*`, or explicit review opinion sent to another agent must
     receive a substantive reply with the same `task_id`.
   - `message/received` only proves the target has seen it. It never
     satisfies the reply requirement.
   - A valid target reply is `done/*`, `finding/*`, `decision/*`,
     `blocked/*`, `handoff/*`, `test/*`, or `message/answered`.
   - If an agent disagrees, it must say why and propose the smallest safe
     alternative. Silence is treated as unresolved work.
   - If the original requester later proves the request is obsolete, it may
     close the request with the same `task_id`. For `done`, `release`,
     and `decision`, closeout statuses are `done`, `closed`,
     `superseded`, `merged`, `abandoned`, `completed`, `approved`,
     `cancelled`/`canceled`, or a descriptive underscore-suffixed form
     of one of those stems such as `superseded_availability_ping` or
     `merged_post_merge_ci_green`. For `message`, only `closed`,
     `superseded`, `cancelled`/`canceled`, and their underscore-suffixed
     forms close a request. Status tools report requester closeout as
     `closed`, not as an answer from the target agent.
   - An exact `wake_request/closed` from the original requester closes only
     an earlier `wake_request` with the same `task_id`. It does not close a
     different request type, does not close another task through a shared PR
     payload, and never counts as an answer from the target agent. Other
     terminal-looking wake statuses do not close requests.
   - `done/request` is still request-like work. Do not use it as a
     closeout status.

7. Alternate review loops.
   - For meaningful bridge/protocol/source changes, run the
     architect/security/reliability loop before merge.
   - Prefer alternating ownership: if Claude implemented the last fix,
     Codex should review or run the next internal iteration; if Codex
     implemented the last fix, Claude should review or run the next
     internal iteration.
   - The agent running the iteration owns the fixes it discovers unless
     that would conflict with another active write claim.
   - **Process-isolated three-role review (R20.5, R16 ratification).**
     Use `.agent-bridge/bin/Invoke-RoleReview.ps1 -Target <pr|branch>`
     to run architect, security, and reliability as three independent
     processes that emit three separate sub-task bridge events plus
     one synthesis event. The synthesis event references all three
     sub-task_ids in its message body so disagreement surfaces
     visibly. The legacy "three labels in one Codex paragraph"
     pattern (architect: ... ; security: ... ; reliability: ...) is
     **deprecated** because the three perspectives collapse into one
     pass with no tooling-level signal that the review degraded. New
     reviews touching bridge/protocol/source SHOULD use the wrapper.
     A `-DryRun` mode is available for smoke testing the wiring
     without executing the orchestrator subprocess.
   - **Wake-on-event substrate (R23.0).** Pure pull-only polling can
     deadlock for 270 s+ when both agents are idle. Each session shell
     should run `Watch-Bridge.ps1` as a background job (the
     `Start-AgentBridgeSession.ps1` bootstrap launches it automatically
     unless `$env:WAGGLE_BRIDGE_WAKE_ENABLED=0` or `-SkipWakeWatcher`).
     The watcher polls `shared/events.jsonl` once per second; when a
     new event whose `to` targets the watched agent appears, it
     creates `<bridgeRoot>/wake_<agent>` as a dirty bit. The agent's
     polling loop should call `Test-BridgeWake.ps1 -Agent <name>` each
     iteration as a fast pre-check before any heavier
     `Read-AgentBridge.ps1` call; finding the file consumes it. End-
     to-end measured smoke latency (`Test-BridgeWakeOnEventSmoke.ps1`)
     is < 300 ms when the watcher is warm.
   - **Cursor monitor for active conversation (R23.3).** Wake files are a
     dirty bit, not a transcript. Agents with a chat/terminal monitor surface
     should run `Monitor-AgentBridge.ps1 -Agent <me> -FromAgent <other>` to
     print each new substantive event exactly once. The monitor stores a
     line-count cursor under `shared/monitor_<agent>...cursor.json`, initializes
     new cursors at "now" to avoid historical floods, and excludes
     heartbeat/liveness/wake_request plus ACK-only `message/received` events.
     This prevents the "same replay" failure mode where a shell loop keeps
     showing the last old event forever. Verified by
     `Test-BridgeMonitorCursorSmoke.ps1`.
   - **Background-job cleanup (R23.1.1).** The wake (R23.0) and
     heartbeat (R23.1) background jobs are stopped automatically on
     normal PowerShell shutdown via a `PowerShell.Exiting` event
     handler registered once per session in
     `Start-AgentBridgeSession.ps1`. **Hard kills (Ctrl+C close on
     host crash, OOM, BSOD)** do NOT trigger the handler. After such
     a kill, the next bootstrap in the same host inherits orphans;
     run `Stop-AgentBridgeSession.ps1` (optionally with `-Agent
     <name>`) to clean them up. Without this, dead agent shells keep
     emitting stale helper traffic or bumping `last_heartbeat_utc`
     on their own claims, defeating stale-lease auto-release. Current
     heartbeat helpers are claim-aware and exit after bounded no-claim
     idle time, but hard-killed old helpers should still be cleaned up.
     Verified by `Test-BridgeJobCleanupSmoke.ps1`.

## Commands

From the repo root:

```powershell
# See recent cross-agent state and active claims.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Read-AgentBridge.ps1 -Agent codex -ShowClaims -Tail 40

# Preferred reboot/new-shell bootstrap. Dot-source this in the shell that will
# launch the agent so AGENT_BRIDGE_RUNTIME_ROOT and AGENT_BRIDGE_RUN_ID persist.
# By default also launches the R23.0 wake-on-event watcher as a background job;
# pass -SkipWakeWatcher (or set $env:WAGGLE_BRIDGE_WAKE_ENABLED=0) to opt out.
# R23.1 also launches a heartbeat job; pass -SkipHeartbeatJob (or set
# $env:WAGGLE_BRIDGE_HEARTBEAT_ENABLED=0) to opt out.
. .\.agent-bridge\bin\Start-AgentBridgeSession.ps1 -Agent codex

# R23.2 preferred write-capable startup: create a dedicated worktree first,
# then require that the agent session is not running in the primary shared
# repo.
. .\.agent-bridge\bin\Start-AgentBridgeWorktreeSession.ps1 -Agent codex

# Lower-level debug path:
$wt = & .\.agent-bridge\bin\New-AgentBridgeWorktree.ps1 -Agent codex -TaskId "r22.1a-hotpath-benchmark" -Base origin/main
cd $wt.worktree_path
. .\.agent-bridge\bin\Start-AgentBridgeSession.ps1 -Agent codex -RequireDedicatedWorktree

# Fast pre-check used by the agent's polling loop. Returns $true exactly once
# after a targeted event arrives; the wake file is consumed on read.
& .\.agent-bridge\bin\Test-BridgeWake.ps1 -Agent codex

# Real-time/cursor monitor for the active conversation. Prints only new
# substantive events from the other agent and advances a cursor so the same
# event is not replayed on the next poll.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Monitor-AgentBridge.ps1 -Agent codex -FromAgent claude -PollIntervalMs 10000

# Read without writing received ACKs.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Read-AgentBridge.ps1 -Agent codex -ShowClaims -NoAckReceived -Tail 40

# Claim a write task.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Claim-AgentTask.ps1 -Agent codex -TaskId "review-claude-diff" -Summary "Read-only review of Claude diff" -Mode read-only

# Claim an implementation task with a write scope.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Claim-AgentTask.ps1 -Agent claude -TaskId "fix-ledger-contract" -Summary "Fix ledger contract schema drift" -Mode write -WriteScope "docs/design/ledger_contract.md","orchestrator/Test-LedgerContract.ps1"

# Publish a status, finding, test result, or handoff.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Write-AgentEvent.ps1 -Agent codex -Type finding -TaskId "review-claude-diff" -Status open -Message "ledger_contract.md still claims score_categories[] is required"

# Reply to a request: preserve the original task id so continuity is machine-checkable.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Write-AgentEvent.ps1 -Agent codex -Type done -TaskId "validators-property-gate-fix-prompt-review-2026-05-09" -Status approved -To claude -Message "Path X approved by Codex consensus"

# See active claims, unresolved requests, contribution counts, recent
# substantive events, and next-action signals.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Get-AgentBridgeStatus.ps1

# Keep the human console readable while preserving full JSON output.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Get-AgentBridgeStatus.ps1 -MaxUnresolved 10

# PREFERRED: branch-aware git wrapper. Pass-through for status/log/diff/...;
# blocks switch/checkout/merge/rebase/pull when another agent holds an
# active claim. -Force is operator/system only.
#
# IMPORTANT: invoke via -Command (not -File). PowerShell -File mode treats
# `--` as an ambiguous parameter and the trailing git args do not bind to
# -GitArgs. The -Command form preserves them via ValueFromRemainingArguments.
powershell -NoProfile -ExecutionPolicy Bypass -Command "& .\.agent-bridge\bin\Invoke-BridgeGit.ps1 -Agent claude -- switch main"

# Passive pre-flight check (no git execution). Exit 0 = safe, exit 2 =
# another agent holds an active write claim. Useful for scripting decisions
# before a branch-moving operation. -File mode is fine here (no `--`).
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Test-BridgeBranchSwitchSafe.ps1 -Agent claude

# End-to-end smoke test of the branch-guard contract. Creates a temporary
# foreign-agent claim, verifies blocked + pass-through + -Force-rejected
# behavior, then releases the claim. Run after any change to the guards.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Test-BridgeGuardSmoke.ps1

# End-to-end smoke test of the reboot bootstrap helper. Uses a temp runtime
# root and verifies the helper creates bridge dirs and emits liveness/active.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Test-BridgeSessionBootstrapSmoke.ps1

# End-to-end smoke test of per-agent worktree creation/isolation.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Test-BridgeWorktreeIsolationSmoke.ps1

# End-to-end smoke test of the one-command worktree session bootstrap.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Test-BridgeWorktreeBootstrapSmoke.ps1

# End-to-end smoke test of cursor monitor semantics: no historical flood,
# no ACK/heartbeat noise, no same-event replay, and live append detection.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Test-BridgeMonitorCursorSmoke.ps1

# Release a task claim.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Release-AgentTask.ps1 -Agent codex -TaskId "review-claude-diff" -Status done -Message "Review complete; 2 medium findings"
```

## Event Types

`status`, `intent`, `claim`, `release`, `message`, `finding`,
`decision`, `test`, `blocked`, `handoff`, `done`, `heartbeat`,
`wake_request`, `liveness`.

## Continuity Protocol (added 2026-05-09)

The bridge has three live consumers with different liveness models:

- **Claude Code**: Monitor (`Get-Content -Wait`) on `events.jsonl`
  fires per-line callbacks while the session is open. Pollable in
  near-real-time, but only for the duration of a single session.
- **Codex CLI**: polls the bridge while a Codex turn is actively
  running. When Codex returns its final response, polling stops
  until the operator sends the next prompt. Codex is NOT a
  background daemon.
- **GPT (third model)**: does NOT poll the bridge. Operator pastes
  artifacts to GPT's web UI out-of-band; GPT's reply is pasted
  back. GPT is read-only-on-demand.

These three models in combination produce a continuity-bug:
when one agent finishes its turn before the other has reacted, the
loop stalls until the operator sends a manual nudge. The fix below
makes liveness explicit so the operator and the agents can see
who is awake.

### Liveness markers

Each agent SHOULD emit `liveness` events at session boundaries:

- `liveness/active` when the agent starts its turn (immediately
  after running `Read-AgentBridge.ps1`).
- `liveness/sleeping` when the agent's turn is about to end and the
  next event will not come from this agent until externally woken.

`liveness/active` MAY be re-sent at most every 60 seconds during
long-running work as an implicit heartbeat. A `liveness/active` more
than 5 minutes old is considered stale; the agent is asleep.

### Heartbeat events

While holding an active write claim or actively producing review
output, the holding agent SHOULD emit `heartbeat/active` events at
60-second intervals so other agents and the operator can see the
turn is still live. Heartbeat-only traffic without a matching active
claim is not substantive progress and must not close or satisfy a
`wake_request`.

A claim with no `heartbeat` event within 5 minutes is considered
**dropped** and may be re-claimed by another agent (or the operator
may release it via `Release-AgentTask.ps1`).

### Wake requests

When an agent needs another to act and has no other in-flight work,
it MUST emit a `wake_request/<severity>` event in addition to the
normal `handoff`. This is the explicit signal to the operator that
the next loop turn is blocked on the named target agent.

```powershell
.\.agent-bridge\bin\Write-AgentEvent.ps1 -Agent claude -Type wake_request `
    -Severity high -To codex `
    -Message "PR #124 fix branch ready for re-review; Codex polling needed" `
    -TaskId "wake-codex-for-pr124-rereview"
```

The operator's role on a `wake_request`:

- For `severity: low` → optional; the loop can wait for the next
  natural turn.
- For `severity: medium` → operator should pump the target agent
  within ~10 minutes by pasting the standard "read bridge and
  continue" prompt.
- For `severity: high` → pump immediately. A high-severity wake
  request paired with a stale `liveness/sleeping` event is the
  fastest way to surface a stalled loop.

`Read-AgentBridge.ps1 -ShowLiveness` (added together with this
protocol update) reports the latest `liveness` and `heartbeat` per
agent, plus any open `wake_request` events, so the operator can see
loop state at a glance.

A `wake_request` is no longer considered open after the target agent
emits any later bridge activity (`liveness`, `heartbeat`, `message`,
`done`, `finding`, `test`, `decision`, `handoff`, `blocked`, `claim`,
or `release`) or after a later `wake_request/closed` event with the
same `task_id`. The continuity section may still show unresolved work;
the wake list is only for "the target has not woken yet."

### What does NOT change

- Pure-test exception in the GPT consensus gate is unchanged.
- CLAUDE.md rule 9 autonomous-merge guardrails are unchanged.
- Claim ownership and write-scope conflict rules are unchanged.
- Operator escalation for force-push, hard-reset, and
  HUMAN_APPROVAL.yaml is unchanged.

The continuity protocol does **not** make any agent into a daemon.
It gives the loop a shared vocabulary for "I'm awake / I'm asleep /
please wake X" so the operator pumps the loop with full context
rather than guessing whose turn it is.

## Received ACK Protocol (added 2026-05-09)

`Read-AgentBridge.ps1 -Agent <agent>` records a lightweight received
acknowledgement for each latest incoming request-like event by writing:

- `type`: `message`
- `status`: `received`
- `task_id`: the original request's exact `task_id`
- `to`: the original sender
- `payload.request_ts_utc`: the timestamp of the request being acked

This separates three states that used to collapse together:

- `WAITING-FOR-*`: the target has not acknowledged the latest request.
- `RECEIVED-BY-*`: the target read the latest request, but has not
  answered it.
- `answered-by-*`: the target emitted a non-ACK event with the same
  `task_id`.

`message/received` never counts as an answer. Agents still close work
with the normal `done`, `finding`, `blocked`, `handoff`, `test`, or
other substantive event using the same `task_id`. The ACK is deduped by
`agent + task_id + request_ts_utc` so repeated bridge reads do not spam
the event log for the same request.

## Polymorphic Continuity Classification (added 2026-05-11)

Bridge readers must tolerate richer domain event types. A targeted event with
the same `task_id` from the requested agent is a substantive answer unless it
is an ACK (`message/received`, `seen`, `acknowledged`) or infrastructure
traffic (`heartbeat`, `liveness`, `wake_request`). This prevents custom events
such as `ownership_proposal/open` from being silently dropped by polling.

Directed events are request-like when they have `to`, `task_id`, and an
open/proposal/request-style status. Readers split comma-separated `to` values
so `to: "claude,operator"` is tracked per target instead of waiting for a
nonexistent combined agent named `claude,operator`.

## Files

- `.agent-bridge/shared/events.jsonl` - append-only merged event stream.
- `.agent-bridge/shared/last_codex.json` - latest Codex event.
- `.agent-bridge/shared/last_claude.json` - latest Claude event.
- `.agent-bridge/outbox/<agent>/<yyyy-mm-dd>.jsonl` - per-agent event log.
- `.agent-bridge/work_queue/claims/*.json` - active task claims.
- `.agent-bridge/work_queue/done/*.json` - released task claims.
- `.agent-bridge/inbox/<agent>/*.md` - one-off messages an agent should read.

`Read-AgentBridge.ps1` reads the last 50000 events for continuity
analysis by default. This is intentionally larger than the original
5000-line window so two agents emitting heartbeat events every minute do
not silently age active task history out of the continuity view.

## Startup Instruction For Both Agents

At session start, read this file and run `Read-AgentBridge.ps1` with
your agent name. During the session, use the bridge commands above
instead of relying on the operator to relay progress.
