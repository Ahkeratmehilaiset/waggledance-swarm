# WaggleDance: reboot recovery

The reboot entry point is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Python\start-wd-all.ps1
```

Its non-mutating verification mode is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Python\start-wd-all.ps1 -DryRun
```

Run `-DryRun` first after Windows sign-in. If it exits `0`, run the first command
once and leave its four Windows Terminal tabs open. The fifth, headless Tools
lane and exactly five real-time bridge watchers are reconciled by the same
command through `WD-Supervisor`.

The command performs a whole-fleet preflight before opening a window. It then
updates Codex and Claude Code with their supported `update` commands, resolves
the authenticated Grok CLI's current provider-default model, and resumes each
verified persistent C-drive worktree at its current branch and HEAD. The
committed branch/HEAD remains a recorded deployment baseline. Recovery never
fetches, checks out, resets, creates a branch, or creates a replacement
worktree.

The explicit runtime choices are:

- Lead: `gpt-5.6-sol`, effort `max`;
- Tools: `gpt-5.6-terra`, effort `high`;
- RCO1 and RCO2: Claude `sonnet`, effort `max`;
- Fable: Claude `fable`, effort `max`.

Durable bridge state, handoffs, current Git worktrees, and pushed savepoints
are the resume substrate; a provider transcript is not the authority. A green
checkpoint must still be saved with `tools/savepoint.ps1`, because no launcher
can reconstruct bytes that were never durably written before a power loss.

Before each lane invokes its model, it writes one
`target_state_manifested` status event for that reboot run. The hash-anchored
target is `WD_SWARM_TARGET_STATE_V1.md`; the event grants no capability or
authority. Failure to append the event prevents that lane from launching.

The Grok provider default is the authoritative, non-hard-coded choice available
to this account after the CLI update. The resolver verifies that it occurs
exactly once in the provider's available-model list and records both the model
and exact high-effort invocation. It does not guess a “strongest” model from
version-like names.

The resolved Grok model and exact invocation examples are written to:

- `C:\Python\WD_GROK_MODEL_CURRENT.json`
- `C:\Python\WD_GROK_MODEL_CURRENT.md`

## Source and integrity

The Git repository is the only source of truth. A pushed commit is installed
into `C:\Python\wd-reboot-bundles\<full-commit-sha>`. Machine-local
`start-wd-*.ps1` files are small, hash-checking wrappers only.

Current pointers:

- `C:\Python\WD_REBOOT_STATE_CURRENT.json`
- `C:\Python\WD_REBOOT_STATE_CURRENT.md`
- `C:\Python\WD_REBOOT_INTEGRITY_CURRENT.json`
- `C:\Python\WD_REBOOT_INTEGRITY_CURRENT.sha256`

The older `WD_REBOOT_INTEGRITY_20260725.sha256` and dated reboot-state file are
historical records. They do not override a newer handoff or the live bridge.

## Authority and safety

Startup state precedence is:

1. live bridge state, read without acknowledging stale events;
2. newer fleet and per-agent handoffs;
3. the current reboot pointer;
4. dated snapshots as historical evidence.

Recovery grants no merge, deploy, signature, canary, runtime-authority, or
`claim_safe` permission. `WD-BridgeMergeDriverStandingOneShot` is deliberately
disabled. Neither the launcher nor the supervisor contains an enable path for
it.

`-DryRun` performs read-only probes only: no CLI update, cache write, bridge
event, task mutation, process launch, report write, checkout, or fetch.
