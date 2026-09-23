# Agent bridge bootstrap

Purpose: after a reboot or a fresh PowerShell window, Claude and Codex must
recover the same bridge state, know who owns work, and continue the alternating
bridge/orchestrator loop without operator paste-relay.

This file is the reboot runbook. New sessions should read it before relying on
conversation history.

## Persistent paths

- Source repo and only source of truth: `C:\Python\project2`
- Per-agent worktree root: `C:\Python\waggledance-agent-worktrees`
- Shared bridge runtime root: `C:\Python\project2-master\.agent-bridge`
- Bridge protocol: `.agent-bridge\BRIDGE_PROTOCOL.md`
- Status command: `.agent-bridge\bin\Get-AgentBridgeStatus.ps1`
- Reader command: `.agent-bridge\bin\Read-AgentBridge.ps1`

The runtime root is not a source repository and must never be used for `git`,
source edits, tests, commits, or unpinned executable-source selection. Fresh
worktrees come only from `C:\Python\project2` and point to the separate runtime
root through `AGENT_BRIDGE_RUNTIME_ROOT`. Executable runtime copies are eligible
only when the deployment preflight binds their bytes to the exact canonical Git
commit. A path below `C:\Python\project2-master` is acceptable as `-RuntimeRoot`
data or as such a hash-pinned runtime copy; it is not an acceptable
`-SourceRepoRoot`, `-Worktree`, repo, or Git common directory.

## Read-only deployment attestation — HOLD

`tools\bridge_runtime_deployment_gate.py` is audit-only. Its production CLI
accepts only a full 40-hex expected commit and optional JSON rendering. It does
not accept process snapshots, Scheduled Task snapshots, repo/runtime overrides,
or fixture evidence. Production mode is fixed to the clean physical
`C:\Python\project2` checkout and requires
`HEAD == expected commit == refs/remotes/origin/main` under a sanitized Git
environment. Replace refs, grafts, alternate object stores, shallow/promisor
state, index masking, dirty source, and object-integrity failures all refuse.
Before every repository-sensitive Git subprocess, the physical local config is
strictly parsed against a small inert allowlist. Includes, external attributes
or excludes, filters, diff/textconv and merge drivers, aliases, hooks, partial
clone/promisor settings, and fsck skip/severity overrides refuse. Config bytes
are compared again after Git reads; ignored files and tracked physical bytes
that differ from their stage-0 blobs also refuse.

The v2 Windows collector takes process and Task Scheduler samples A and B during
one invocation. Its reducer requires an exact raw schema and types, rejects
duplicate identities, binds observed candidates to host/boot/time, process and
parent creation identities, task definitions, and pinned executable bytes, and
checks literal runtime dependencies it can independently discover. The collector
never starts, stops, registers, enables, disables, or rewrites a bridge process
or Scheduled Task. Reports expire after a short freshness window and are
informational; saved JSON is not replayable authority.

Injected repositories, clocks, executables, or evidence are test fixtures only.
Even an exact fixture match returns `MATCH_TEST_ONLY`, `ok=false`, and exit 3.
The production wrapper has no injection parameters and is the only code path
reserved for a future `LIVE_MATCH`/0. In this version, authoritative success is
unreachable: non-heuristic proof of complete process/task scope and complete
collector/runtime dependency discovery is not implemented. Encoded/dynamic
wrappers in an explicitly scoped ancestry/descendant chain refuse, but this is
not documented as proof of the absence of every possible hidden wrapper.

Deployment remains intentionally blocked by
`configs/bridge_runtime_deployment.v2.json`. Do not populate production host,
toolchain, collector, runtime, process, or Scheduled Task hashes until:

1. PR-A's spool/WAL writer is committed and pushed at an exact SHA.
2. Direct Python writers in `bridge_loop_tick.py`,
   `idle_protocol_activate.py`, and `close_bridge_rco_request.py` are migrated,
   tested, committed, and pushed.
3. The integrated source is merged to a clean canonical `origin/main`.
4. The Python image hosting the gate, Git, PowerShell, collector, and every
   runtime/config/module/DLL input are pinned and independently verified.
5. A reviewed non-heuristic scope and dependency proof replaces the explicit
   `live_authority_hold` guard.
6. A separately authorized operation deploys those exact bytes and restarts all
   affected writers before any authoritative live A/B attestation is enabled.

Every authority flag in the gate report remains false. The gate audits
eligibility; it does not deploy.

## One-time setup after reboot

Run once in any PowerShell if the runtime root may not exist:

```powershell
New-Item -ItemType Directory -Force `
  C:\Python\project2-master\.agent-bridge\shared,`
  C:\Python\project2-master\.agent-bridge\work_queue,`
  C:\Python\project2-master\.agent-bridge\outbox,`
  C:\Python\project2-master\.agent-bridge\inbox | Out-Null
```

Optional persistent user environment variable:

```powershell
[Environment]::SetEnvironmentVariable(
  'AGENT_BRIDGE_RUNTIME_ROOT',
  'C:\Python\project2-master\.agent-bridge',
  'User'
)
```

Even if that user env var is set, each new agent shell should set the process
env var explicitly so the current session is unambiguous.

## Preferred isolated worktree startup

For real parallel Claude+Codex work, do not run both agents from
`C:\Python\project2`. Create one physical git worktree per agent/task
first, then bootstrap the bridge from inside that worktree. This removes the
branch-switch race entirely: one agent can switch, commit, or test without
moving the other agent's working directory.

Run from the primary source repo:

```powershell
cd C:\Python\project2
. .\.agent-bridge\bin\Start-AgentBridgeWorktreeSession.ps1 `
  -Agent codex `
  -SourceRepoRoot C:\Python\project2 `
  -WorktreeRoot C:\Python\waggledance-agent-worktrees `
  -RuntimeRoot C:\Python\project2-master\.agent-bridge
```

Claude uses the same command with `-Agent claude`. Dot-source it so the shell
that launches the agent keeps the new worktree location plus
`AGENT_BRIDGE_RUNTIME_ROOT` and `AGENT_BRIDGE_RUN_ID`.

If you need an explicit base refresh first:

```powershell
cd C:\Python\project2
git fetch origin main
. .\.agent-bridge\bin\Start-AgentBridgeWorktreeSession.ps1 `
  -Agent codex -Fetch `
  -SourceRepoRoot C:\Python\project2 `
  -WorktreeRoot C:\Python\waggledance-agent-worktrees `
  -RuntimeRoot C:\Python\project2-master\.agent-bridge
```

Both worktrees still write to the same
`C:\Python\project2-master\.agent-bridge` runtime root, so bridge events,
claims, wake files, and heartbeat state remain shared.

The lower-level two-step primitive remains available for debugging:

```powershell
$wt = & .\.agent-bridge\bin\New-AgentBridgeWorktree.ps1 `
  -Agent codex `
  -TaskId "codex-session-$((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ'))" `
  -SourceRepoRoot C:\Python\project2 `
  -WorktreeRoot C:\Python\waggledance-agent-worktrees `
  -RuntimeRoot C:\Python\project2-master\.agent-bridge `
  -Base origin/main

cd $wt.worktree_path
. .\.agent-bridge\bin\Start-AgentBridgeSession.ps1 -Agent codex -RequireDedicatedWorktree
```

## Claude Code shell

Fallback shared-worktree bootstrap. Use this only for read-only review,
operator maintenance, or when there is no parallel writer. Dot-source it so
the environment variables remain in the shell that launches Claude Code:

```powershell
cd C:\Python\project2
. .\.agent-bridge\bin\Start-AgentBridgeSession.ps1 `
  -Agent claude `
  -RuntimeRoot C:\Python\project2-master\.agent-bridge
```

Then launch Claude Code from the same shell.

Manual fallback:

```powershell
cd C:\Python\project2
$env:AGENT_BRIDGE_RUNTIME_ROOT = 'C:\Python\project2-master\.agent-bridge'
$env:AGENT_BRIDGE_RUN_ID = "claude-$((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ'))"
git pull --ff-only

powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Test-BridgeRuntimeRootSmoke.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Test-BridgeGuardSmoke.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Read-AgentBridge.ps1 -Agent claude -ShowClaims -Tail 80
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Get-AgentBridgeStatus.ps1 -MaxUnresolved 15

# Optional live Claude Code Monitor-tool command.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Monitor-AgentBridge.ps1 -Agent claude -FromAgent codex -PollIntervalMs 10000

. .\orchestrator\Start-WaggleSession.ps1 -ConfigPath .\orchestrator.config.json
```

Then launch Claude Code from the same shell. The session must keep the env vars
above.

## Codex shell

Fallback shared-worktree bootstrap. Use this only for read-only review,
operator maintenance, or when there is no parallel writer. Dot-source it so
the environment variables remain in the shell that launches Codex:

```powershell
cd C:\Python\project2
. .\.agent-bridge\bin\Start-AgentBridgeSession.ps1 `
  -Agent codex `
  -RuntimeRoot C:\Python\project2-master\.agent-bridge
```

Then launch Codex from the same shell.

Manual fallback:

```powershell
cd C:\Python\project2
$env:AGENT_BRIDGE_RUNTIME_ROOT = 'C:\Python\project2-master\.agent-bridge'
$env:AGENT_BRIDGE_RUN_ID = "codex-$((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ'))"
git pull --ff-only

powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Test-BridgeRuntimeRootSmoke.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Test-BridgeGuardSmoke.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Read-AgentBridge.ps1 -Agent codex -ShowClaims -Tail 80
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Get-AgentBridgeStatus.ps1 -MaxUnresolved 15

# Optional during an active Codex turn or bounded wait.
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Monitor-AgentBridge.ps1 -Agent codex -FromAgent claude -PollIntervalMs 10000
```

Then launch Codex from the same shell. The session must keep the env vars
above.

## Bootstrap smoke test

After bridge bootstrap changes, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Test-BridgeSessionBootstrapSmoke.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Test-BridgeWorktreeIsolationSmoke.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Test-BridgeWorktreeBootstrapSmoke.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\.agent-bridge\bin\Test-BridgeMonitorCursorSmoke.ps1
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
   If a live monitor surface is available, start or resume
   `Monitor-AgentBridge.ps1 -Agent <me> -FromAgent <other>` so new
   substantive bridge events appear without replaying old history.
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

