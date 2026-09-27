# Lane launch environment

`start-wd-agent.ps1` and `start-wd-tools-consumer.ps1` remove the inherited Claude
Code session markers from their own process environment as their first action,
before anything is started. Every lane process, and every helper the launcher
starts, then begins without another session's identity.

The launcher removes exactly these variables:

- `CLAUDECODE`
- `CLAUDE_CODE_CHILD_SESSION`
- `CLAUDE_CODE_SESSION_ID`
- `CLAUDE_PID`
- `CLAUDE_CODE_ENTRYPOINT`
- `CLAUDE_CODE_SESSION_ATTENDED`
- `CLAUDE_CODE_EXECPATH`
- `CLAUDE_CODE_MESSAGING_SOCKET`
- `CLAUDE_CODE_MESSAGING_TOKEN`

The launcher prints the names it removed on a `scrubbed:` line and never prints
their values. It leaves operator configuration alone, including
`CLAUDE_CODE_DISABLE_CRON` (which the launcher sets itself), provider variables
and `CLAUDE_CONFIG_DIR`.

## Why

A Claude Code session exports these markers to the tool shells it runs. On
2026-09-26 the RCO lanes were relaunched from a terminal window opened in such a
shell. claude-rco-1 then found the cause by reading the installed CLI and the
process environment blocks. Claude Code 2.1.282 treats an interactive session
that carries `CLAUDE_CODE_CHILD_SESSION` as a nested child session. It writes no
`<session>.jsonl` and no `sessions/<pid>.json`, so the lane's conversation is
not persisted and cannot be resumed. The same window also passed that session's
peer pipe and token to the Lead and Tools processes started from it.

On 2026-09-27 a relaunch with these variables removed persisted normally on the
same 2.1.282.

## What is still the operator's rule

The launchers cover every lane they start. Anything started some other way from
an agent's tool shell is not covered, for example a terminal window, a Claude
Code session or a script. Start the fleet from a shell the operator opened, such
as `C:\Python\start-wd-all.ps1 -Auto` after a reboot.
