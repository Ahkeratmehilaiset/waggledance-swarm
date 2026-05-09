# Agent Bridge Protocol

Purpose: let Claude and Codex coordinate without the operator relaying
"done", "continue", or "who owns this file" messages.

This is a runtime bridge, not the source of truth. It lives under
`.agent-bridge/` and is safe to clear between sessions.

## Core Rules

1. Read the bridge before you wait.
   - If you are about to stop because you need the other agent, first run
     `Read-AgentBridge.ps1` and check whether the other agent already
     published `done`, `handoff`, `blocked`, or `finding` events.
   - Always pass your own `-Agent` name. The bridge reader prints a
     continuity section that marks incoming requests to you as `OPEN`
     or `answered`, and outgoing requests you sent as `WAITING-FOR-*`
     or `answered-by-*`, based on matching `task_id` values.

2. Claim write work before editing.
   - A write task must have an active claim with `write_scope`.
   - Do not edit a path covered by another active write claim.
   - Read-only review can use `-Mode read-only` and does not block writers.

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

5. Operator escalation is only for external permissions, destructive
   actions, or unresolved claim conflicts.

## Commands

From the repo root:

```powershell
# See recent cross-agent state and active claims.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Read-AgentBridge.ps1 -Agent codex -ShowClaims -Tail 40

# Claim a write task.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Claim-AgentTask.ps1 -Agent codex -TaskId "review-claude-diff" -Summary "Read-only review of Claude diff" -Mode read-only

# Claim an implementation task with a write scope.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Claim-AgentTask.ps1 -Agent claude -TaskId "fix-ledger-contract" -Summary "Fix ledger contract schema drift" -Mode write -WriteScope "docs/design/ledger_contract.md","orchestrator/Test-LedgerContract.ps1"

# Publish a status, finding, test result, or handoff.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Write-AgentEvent.ps1 -Agent codex -Type finding -TaskId "review-claude-diff" -Status open -Message "ledger_contract.md still claims score_categories[] is required"

# Reply to a request: preserve the original task id so continuity is machine-checkable.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Write-AgentEvent.ps1 -Agent codex -Type done -TaskId "validators-property-gate-fix-prompt-review-2026-05-09" -Status approved -To claude -Message "Path X approved by Codex consensus"

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
turn is still live.

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

## Files

- `.agent-bridge/shared/events.jsonl` - append-only merged event stream.
- `.agent-bridge/shared/last_codex.json` - latest Codex event.
- `.agent-bridge/shared/last_claude.json` - latest Claude event.
- `.agent-bridge/outbox/<agent>/<yyyy-mm-dd>.jsonl` - per-agent event log.
- `.agent-bridge/work_queue/claims/*.json` - active task claims.
- `.agent-bridge/work_queue/done/*.json` - released task claims.
- `.agent-bridge/inbox/<agent>/*.md` - one-off messages an agent should read.

## Startup Instruction For Both Agents

At session start, read this file and run `Read-AgentBridge.ps1` with
your agent name. During the session, use the bridge commands above
instead of relying on the operator to relay progress.
