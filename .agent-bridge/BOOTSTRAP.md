# Agent bridge bootstrap

Purpose: after a reboot or a fresh PowerShell window, Claude and Codex must
recover the same bridge state, know who owns work, and continue the alternating
bridge/orchestrator loop without operator paste-relay.

This file is the reboot runbook. New sessions should read it before relying on
conversation history.

## Persistent paths

- Source repo: `C:\Python\project2-master`
- Per-agent worktree root: `C:\tmp\waggledance-agent-worktrees`
- Shared bridge runtime root: `C:\Python\project2-bridge-runtime`
- Bridge protocol: `.agent-bridge\BRIDGE_PROTOCOL.md`
- Status command: `.agent-bridge\bin\Get-AgentBridgeStatus.ps1`
- Reader command: `.agent-bridge\bin\Read-AgentBridge.ps1`

The runtime root is intentionally outside any one worktree. If Claude and
Codex later use separate worktrees, both still point to the same runtime root
through `AGENT_BRIDGE_RUNTIME_ROOT`.

## One-time setup after reboot

Run once in any PowerShell if the runtime root may not exist:

```powershell
New-Item -ItemType Directory -Force `
  C:\Python\project2-bridge-runtime\shared,`
  C:\Python\project2-bridge-runtime\work_queue,`
  C:\Python\project2-bridge-runtime\outbox,`
  C:\Python\project2-bridge-runtime\inbox | Out-Null
```

Optional persistent user environment variable:

```powershell
[Environment]::SetEnvironmentVariable(
  'AGENT_BRIDGE_RUNTIME_ROOT',
  'C:\Python\project2-bridge-runtime',
  'User'
)
```

Even if that user env var is set, each new agent shell should set the process
env var explicitly so the current session is unambiguous.

## Preferred isolated worktree startup

For real parallel Claude+Codex work, do not run both agents from
`C:\Python\project2-master`. Create one physical git worktree per agent/task
first, then bootstrap the bridge from inside that worktree. This removes the
branch-switch race entirely: one agent can switch, commit, or test without
moving the other agent's working directory.

Run from the primary source repo:

```powershell
cd C:\Python\project2-master
git fetch origin main

$wt = & .\.agent-bridge\bin\New-AgentBridgeWorktree.ps1 `
  -Agent codex `
  -TaskId "codex-session-$((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ'))" `
  -Base origin/main

cd $wt.worktree_path
. .\.agent-bridge\bin\Start-AgentBridgeSession.ps1 -Agent codex -RequireDedicatedWorktree
```

Claude uses the same pattern with `-Agent claude`. Both worktrees still write
to the same `C:\Python\project2-bridge-runtime` runtime root, so bridge
events, claims, wake files, and heartbeat state remain shared.

## Claude Code shell

Fallback shared-worktree bootstrap. Use this only for read-only review,
operator maintenance, or when there is no parallel writer. Dot-source it so
the environment variables remain in the shell that launches Claude Code:

```powershell
cd C:\Python\project2-master
. .\.agent-bridge\bin\Start-AgentBridgeSession.ps1 -Agent claude
```

Then launch Claude Code from the same shell.

Manual fallback:

```powershell
cd C:\Python\project2-master
$env:AGENT_BRIDGE_RUNTIME_ROOT = 'C:\Python\project2-bridge-runtime'
$env:AGENT_BRIDGE_RUN_ID = "claude-$((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ'))"
git pull --ff-only

powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Test-BridgeRuntimeRootSmoke.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Test-BridgeGuardSmoke.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Read-AgentBridge.ps1 -Agent claude -ShowClaims -Tail 80
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Get-AgentBridgeStatus.ps1 -MaxUnresolved 15

. .\orchestrator\Start-WaggleSession.ps1 -ConfigPath .\orchestrator.config.json
```

Then launch Claude Code from the same shell. The session must keep the env vars
above.

## Codex shell

Fallback shared-worktree bootstrap. Use this only for read-only review,
operator maintenance, or when there is no parallel writer. Dot-source it so
the environment variables remain in the shell that launches Codex:

```powershell
cd C:\Python\project2-master
. .\.agent-bridge\bin\Start-AgentBridgeSession.ps1 -Agent codex
```

Then launch Codex from the same shell.

Manual fallback:

```powershell
cd C:\Python\project2-master
$env:AGENT_BRIDGE_RUNTIME_ROOT = 'C:\Python\project2-bridge-runtime'
$env:AGENT_BRIDGE_RUN_ID = "codex-$((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ'))"
git pull --ff-only

powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Test-BridgeRuntimeRootSmoke.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Test-BridgeGuardSmoke.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Read-AgentBridge.ps1 -Agent codex -ShowClaims -Tail 80
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Get-AgentBridgeStatus.ps1 -MaxUnresolved 15
```

Then launch Codex from the same shell. The session must keep the env vars
above.

## Bootstrap smoke test

After bridge bootstrap changes, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Test-BridgeSessionBootstrapSmoke.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Test-BridgeWorktreeIsolationSmoke.ps1
```

This uses a temporary runtime root, proves `Start-AgentBridgeSession.ps1`
creates the shared bridge directories, emits `liveness/active`, and leaves the
production runtime root untouched.

The worktree smoke creates a temporary local git repository and proves
`New-AgentBridgeWorktree.ps1` creates separate Claude/Codex worktrees without
moving the source repo branch.

## Resume algorithm

Each agent follows this order after startup:

1. Read bridge state with its own `-Agent` value. This emits
   `message/received` acknowledgements for incoming request-like events.
2. If it has an unresolved incoming request, answer it using the exact same
   `task_id`.
3. If the other agent has an active write claim, do read-only review, targeted
   verification, scout work, or publish a precise `blocked` event.
4. If no one owns useful work, claim the next small task. Prefer:
   - review the other agent's open PR or diff;
   - run architect/security/reliability iteration over bridge or orchestrator
     changes;
   - scout the next WaggleDance core test gap;
   - implement the smallest approved fix from an existing finding.
5. Publish `status`, `finding`, `test`, `decision`, `handoff`, and `done`
   events after meaningful steps.

## Alternating bridge/orchestrator loop

The steady-state loop is:

1. One agent implements or scouts.
2. The other agent reviews or runs architect/security/reliability mode.
3. The finding owner fixes what its iteration found unless that conflicts with
   an active claim.
4. Ownership alternates on the next meaningful change.
5. The bridge records who did what, tests run, open blockers, and next action.

For orchestrator review roles, use the normal review runner against the
iteration package:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\orchestrator\Invoke-WaggleReview.ps1 `
  -ConfigPath .\orchestrator.config.json `
  -SourceIterationId '<iteration-id>' `
  -Role architect

powershell -NoProfile -ExecutionPolicy Bypass -File .\orchestrator\Invoke-WaggleReview.ps1 `
  -ConfigPath .\orchestrator.config.json `
  -SourceIterationId '<iteration-id>' `
  -Role security

powershell -NoProfile -ExecutionPolicy Bypass -File .\orchestrator\Invoke-WaggleReview.ps1 `
  -ConfigPath .\orchestrator.config.json `
  -SourceIterationId '<iteration-id>' `
  -Role reliability
```

After each role, publish a bridge event with the same task id summarizing
verdict, finding count, max severity, and next owner.

## No-idle rule

If the status command reports no active claims and no blocking operator-only
decision, neither agent should wait. Start a small review, scout, test, or fix
and publish a claim first. Idle is only valid when a concrete external blocker
has been published to the bridge.

