# R13 decision record - bridge runtime root + worktree-first hardening

- timestamp: 2026-05-09T13:10:00Z
- branch: waggledance/r13-bridge-runtime-root
- authors: Claude Opus 4.7 + Codex CLI (R13 scout)
- companion scout report:
  [bridge_worktree_feasibility_codex_2026_05_09.md](./bridge_worktree_feasibility_codex_2026_05_09.md)

## Decision

R13 lands as a **small bridge architecture PR** that adds the
`AGENT_BRIDGE_RUNTIME_ROOT` environment variable to all bridge
scripts and documents per-agent worktree setup in
`BRIDGE_PROTOCOL.md`. This PR does NOT yet move Claude or Codex
into separate worktrees; it only makes that move possible without
further changes to the bridge tooling.

The reason for the partial move:

- The structural answer (per-agent worktrees) is correct - the
  scout report shows it removes the shared-branch failure mode by
  construction.
- Forcing the worktree split in the same PR couples a tooling
  change to an operational change. The operator (and Codex) must
  be able to roll forward and back independently.
- This PR is reviewable in one sitting; the worktree split is a
  multi-step setup with environmental side effects that should be
  its own discrete change.

## What this PR contains

1. **`AGENT_BRIDGE_RUNTIME_ROOT` env-var support** in all 7 bridge
   scripts that resolve `$bridgeRoot`:
   - `Claim-AgentTask.ps1`, `Release-AgentTask.ps1`,
     `Read-AgentBridge.ps1`, `Write-AgentEvent.ps1`,
     `Get-AgentBridgeStatus.ps1`, `Invoke-BridgeGit.ps1`,
     `Test-BridgeBranchSwitchSafe.ps1`.
   - When set, the env var overrides the default
     `Split-Path -Parent $PSScriptRoot` resolution. The scripts use
     that runtime root unconditionally, create it if it does not yet
     exist, and fail loudly on malformed/unwritable paths. State
     (`shared/events.jsonl`, `work_queue/claims`, `outbox/`, `inbox/`)
     lands under that directory instead of under each worktree's
     `.agent-bridge/`.
   - Fallback to the existing per-worktree state happens only when
     the env var is unset. Default single-worktree users see no
     change.

2. **`BRIDGE_PROTOCOL.md` doc updates** explaining:
   - the env-var contract;
   - the recommended per-agent worktree topology
     (`C:\Python\project2-claude`, `project2-codex`,
     `project2-master`);
   - junction / `mklink /j` instructions for redirecting per-worktree
     `.agent-bridge\shared` etc. to the shared runtime root, as an
     alternative to the env var.

3. **`Test-BridgeRuntimeRootSmoke.ps1`** — the regression test
   Codex requested. Sets `AGENT_BRIDGE_RUNTIME_ROOT` to a fresh
   non-existing temp dir and verifies all 7 affected scripts use
   it: Write-AgentEvent creates `shared/events.jsonl` under temp;
   Claim creates `work_queue/claims/<task>.json`; Release archives
   to `work_queue/done/`; Read-AgentBridge runs without crashing;
   Get-AgentBridgeStatus runs without crashing. Crucially also
   asserts that the production `.agent-bridge/shared/events.jsonl`
   is NOT touched (no leakage when env redirect is active).
   10 checks total; passes locally.

## What this PR does NOT do (deferred)

- Does NOT move Claude or Codex into separate worktrees. Neither
  agent has been physically relocated; both still operate from
  `C:\Python\project2-master`. The worktree split is a separate
  follow-up that requires:
  - operator decision on the runtime root path
    (`C:\Python\project2-bridge-runtime` per scout recommendation),
  - per-agent venv strategy decision,
  - per-worktree `orchestrator.config.json` rendering convention,
  - a one-time `git worktree add` + `mklink /j` (or env-var)
    setup for each agent.
- Does NOT extend `Invoke-BridgeGit.ps1` to cover destructive
  verbs (`reset --hard`, `clean -fdx`, `stash`, `restore --source`,
  `cherry-pick / revert` with conflicts, submodule ops). The
  scout's recommendation explicitly was to NOT expand the wrapper;
  worktrees are the structural answer to those too.
- Does NOT add an operation lock / lease for TOCTOU. Acknowledged
  as a known limit in `BRIDGE_PROTOCOL.md` rule 2 (already
  documented in PR #155).
- Does NOT change `dangerouslySkipPermissions` defaults or define
  an allowlisted autonomous command profile. That belongs to a
  separate operator-policy PR.

## Compatibility

- Existing default single-worktree users: no change in behavior
  unless they explicitly set `AGENT_BRIDGE_RUNTIME_ROOT`.
- Operator can set the env var globally (system or user scope) or
  per-shell via `$env:AGENT_BRIDGE_RUNTIME_ROOT = '...'`. Either
  works for autonomous agents that inherit the parent shell's
  environment.
- **No silent fallback when env var is set.** Codex blocker
  2026-05-09T13:11Z: a Test-Path-gated fallback would split-brain
  agents on first-run / typo / new-root paths because the env var
  pointing at a missing directory silently reverted to per-worktree
  state. Corrected behavior: when AGENT_BRIDGE_RUNTIME_ROOT is set,
  use it; create the directory if missing (first-run bootstrap);
  fail loudly on malformed paths via -ErrorAction Stop. The
  fallback to per-worktree state happens ONLY when the env var is
  unset.

## Why this beats the alternative "wait until full worktree split"

The env-var support is small (7 lines per script, 9 scripts), low
risk, and unblocks the worktree split whenever the operator wants
to do it. Without this PR, the worktree split is blocked on
modifying every bridge script under each new worktree location.
With this PR, the worktree split is a one-time setup procedure
that doesn't touch the bridge code itself.

## Follow-up tasks

- **R13.5a - session bootstrap helper** (landed after R13): add
  `.agent-bridge/bin/Start-AgentBridgeSession.ps1` and
  `Test-BridgeSessionBootstrapSmoke.ps1` so a reboot/new-shell agent can
  restore the shared runtime root, run id, liveness marker, and bridge status
  from one dot-sourced command instead of copying a manual command block.
- **R13.5b - worktree split** (operator-driven): create
  `C:\Python\project2-claude` + `project2-codex` worktrees; create
  `C:\Python\project2-bridge-runtime\{shared,work_queue,outbox,inbox}`;
  set `AGENT_BRIDGE_RUNTIME_ROOT` for both agent shells.
- **R14 - Invoke-BridgeGit allow-list expansion** (after worktree
  split lands): extend wrapped verbs to cover destructive git
  operations or migrate to a PATH shim; reduce reliance on
  cooperative checking.
- **R15 - operation lock / lease** for TOCTOU between
  Get-ActiveClaims and the git invocation (low priority once
  worktree split is in place because TOCTOU between agents in
  separate worktrees is a non-issue).

## Verification before merge

- All 9 bridge scripts parse clean
  (`[Parser]::ParseFile(...)` returns 0 errors).
- `Test-BridgeGuardSmoke.ps1` -> 7 passed, 0 failed.
- Env-var redirect verified: heartbeat written with
  `AGENT_BRIDGE_RUNTIME_ROOT=$tempDir` lands under
  `$tempDir/shared/events.jsonl`, not under
  `.agent-bridge/shared/events.jsonl`.
