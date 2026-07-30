# Boot after reboot — current quick reference

The supported whole-fleet restore is one command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Python\start-wd-all.ps1
```

Verify the complete plan without changing files, tasks, caches, bridge state,
or processes:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Python\start-wd-all.ps1 -DryRun
```

The installed entry point is a hash-checking wrapper around the exact pushed
bundle recorded in:

- `C:\Python\WD_REBOOT_STATE_CURRENT.json`
- `C:\Python\WD_REBOOT_INTEGRITY_CURRENT.json`
- `C:\Python\WD_REBOOT_INTEGRITY_CURRENT.sha256`

The launcher validates all persistent worktrees before mutation. It does not
fetch, check out, reset, create a branch, or create a replacement worktree.
On a real restore it updates Codex and Claude Code, resolves the current
provider-default Grok model, and resumes the recorded agent generations.
Claude RCO2 and Fable have no model pin and use Claude Code's current default.

Startup state precedence is:

1. live bridge state read without acknowledging stale events;
2. newer fleet and per-agent handoffs;
3. the current reboot pointer;
4. dated reboot snapshots as historical evidence.

Recovery grants no merge, deploy, signature, canary, runtime-authority, or
`claim_safe` permission. `WD-BridgeMergeDriverStandingOneShot` is deliberately
disabled. The reboot launcher and supervisor must never enable it or recommend
that the operator enable it.

The source-controlled implementation and detailed runbook live in
`ops/windows/reboot/`. Machine-local `C:\Python\start-wd-*.ps1` files are
deployment artifacts, not source files.
