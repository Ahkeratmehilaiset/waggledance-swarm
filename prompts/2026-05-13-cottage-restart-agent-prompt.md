# Cottage Restart Prompt - 2026-05-13

Use this after moving networks, waking the laptop, or opening fresh agent
sessions.

## Operator Steps

Start Codex and Claude Code from separate PowerShell windows. In each shell,
dot-source the bridge bootstrap before launching the agent.

Codex shell:

```powershell
cd C:\Python\project2-master
git fetch origin main
$wt = & .\.agent-bridge\bin\New-AgentBridgeWorktree.ps1 `
  -Agent codex `
  -TaskId "codex-resume-20260513-cottage" `
  -Base origin/main
cd $wt.worktree_path
. .\.agent-bridge\bin\Start-AgentBridgeSession.ps1 -Agent codex -RequireDedicatedWorktree
```

Claude shell:

```powershell
cd C:\Python\project2-master
git fetch origin main
$wt = & .\.agent-bridge\bin\New-AgentBridgeWorktree.ps1 `
  -Agent claude `
  -TaskId "claude-resume-20260513-cottage" `
  -Base origin/main
cd $wt.worktree_path
. .\.agent-bridge\bin\Start-AgentBridgeSession.ps1 -Agent claude -RequireDedicatedWorktree
```

## Prompt To Paste To Codex

```text
Read .agent-bridge/BOOTSTRAP.md and .agent-bridge/BRIDGE_PROTOCOL.md.
Then read docs/handoffs/2026-05-13-v3.13.0-release-prep-continuity.md
and prompts/2026-05-13-cottage-restart-agent-prompt.md.
Run Read-AgentBridge.ps1 as codex, resume the latest open bridge task,
respect active claims, use a dedicated worktree, and do not wait silently.
Current known main after PR #372 is de723261a40927f73c14669e5fd23aa373af4e1e.
If no newer bridge blocker exists, coordinate with Claude on the next Sprint 2
or AI-assisted bootstrap task. Keep stable release on hold unless
tools/check_release_gate.py says otherwise.
```

## Prompt To Paste To Claude Code

```text
Read .agent-bridge/BOOTSTRAP.md and .agent-bridge/BRIDGE_PROTOCOL.md.
Then read docs/handoffs/2026-05-13-v3.13.0-release-prep-continuity.md
and prompts/2026-05-13-cottage-restart-agent-prompt.md.
Run Read-AgentBridge.ps1 as claude, resume the latest open bridge task,
respect active claims, use a dedicated worktree, and do not wait silently.
Current known main after PR #372 is de723261a40927f73c14669e5fd23aa373af4e1e.
If no newer bridge blocker exists, coordinate with Codex on the next Sprint 2
or AI-assisted bootstrap task. Keep stable release on hold unless
tools/check_release_gate.py says otherwise.
```

## Current Gate

PR #372 is merged and postmerge sanity passed. Stable release is still held by
the release gate:

- `before_no_earlier_than_date`
- `soak_window_incomplete`
- `soak_evidence_missing`

Do not tag, version-bump, or promote Docker/latest until the gate changes.
