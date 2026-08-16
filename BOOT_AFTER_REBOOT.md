# Boot after reboot — current quick reference

The supported whole-fleet restore is one command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Python\start-wd-all.ps1 -Apply
```

Verify the complete plan without changing files, tasks, caches, bridge state,
or processes:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Python\start-wd-all.ps1 -DryRun
```

After Windows sign-in, run the dry run first. With no mode switch the launcher
also defaults to byte-inert DryRun. If the explicit DryRun exits `0`, run the
restore command once. It updates and re-verifies both agent CLIs before any new
agent starts, then reconciles Tools plus the five real-time bridge watchers and
opens the four interactive lanes. A single stale watcher is replaced only from a verified,
hash-bound old reboot bundle; duplicates and unknown processes fail closed.
An exactly configured `WD-Supervisor` task left Disabled by deployment remains
held throughout DryRun and is enabled only after the complete fleet, bridge
prefix, and spool inventory pass verification. The launcher then demand-starts
that task once and requires a fresh successful scheduler result.

The installed entry point is a hash-checking wrapper around the exact pushed
bundle recorded in:

- `C:\Python\WD_REBOOT_STATE_CURRENT.json`
- `C:\Python\WD_REBOOT_INTEGRITY_CURRENT.json`
- `C:\Python\WD_REBOOT_INTEGRITY_CURRENT.sha256`

The launcher validates all persistent worktrees before mutation. It does not
fetch, check out, reset, create a branch, or create a replacement worktree.
On a real restore it updates Codex and Claude Code, resolves the current
provider-default Grok model, and resumes the canonical current C-drive
worktrees. Lead uses `gpt-5.6-sol/ultra`, Tools uses
`gpt-5.6-terra/high`, RCO1/RCO2 use Claude `sonnet/max`, and Fable uses
Claude `fable/max`.

Each lane emits one hash-bound `target_state_manifested` status and one
unaddressed `append_canary` through the deployed manifest-hashed writer before
its model starts. The canary must complete within five seconds without creating
a new spool file. The target describes the image-backed swarm direction and
does not claim that the capability already exists. Durable bridge state,
handoffs, Git worktrees, and pushed savepoints—not an assumed provider
transcript—determine where work resumes.

Startup state precedence is:

1. live bridge state read without acknowledging stale events;
2. newer fleet and per-agent handoffs;
3. the current reboot pointer;
4. dated reboot snapshots as historical evidence.

Recovery grants no merge, deploy, signature, canary, runtime-authority, or
`claim_safe` permission. `WD-BridgeMergeDriverStandingOneShot` is deliberately
disabled. The reboot launcher and supervisor must never enable it or recommend
that the operator enable it.

The supervisor may still disable/stop a merge-driver task during a watcher
conflict because HOLD is the dominant safety invariant. Watcher and Tools
replacement remains blocked. A durable watcher replacement marker must be
investigated rather than deleted blindly. Recovery is roll-forward: if a later
CLI, Grok, handshake, or terminal step fails after verified helper convergence,
fix the reported cause and repeat DryRun then Apply. Never bulk-replay spool
files as part of reboot recovery.

The source-controlled implementation and detailed runbook live in
`ops/windows/reboot/`. Machine-local `C:\Python\start-wd-*.ps1` files are
deployment artifacts, not source files.
