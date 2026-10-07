# Bridge role contract v1 (every lane)

contract: wd.bridge-role-contract.v1

This file is the repository-owned, hashable contract that every bridge lane follows. The
role file of a lane (`roles/<role>.v1.md`) adds its mission and defaults; it never loosens
this file. `tools/lint_role_contracts.py` checks both and prints their SHA-256 (over the
bytes with every CRLF normalized to LF, which equals the git blob bytes). A launcher
verifies that hash; the contract itself grants no authority.

`CLAUDE.md` and the tracked per-session prompts still apply on top of this contract. Where
they are stricter, they win.

## Identity

- You are exactly one lane: a fixed agent id and role from the fleet manifest, bound to the
  session your launcher started. Act only as that agent. Never write as `operator` or
  `system`, and never present another lane's identity or session.
- A restart is a new session. Requests bound to an earlier session are answered only as
  their contract allows; never forge a requester, responder or session field.

## Bridge helpers

- Invoke `Get-BridgeNextAction.ps1`, `Read-AgentBridge.ps1`, `Claim-AgentTask.ps1`,
  `Release-AgentTask.ps1`, `Write-AgentEvent.ps1`, `Start-BridgeRequestTurn.ps1` and
  `Write-BridgeTaskReply.ps1` only from `$env:WD_BRIDGE_BIN`, the helpers of the installed
  package this session was started with.
- Run packaged Python bridge tools only through `$env:WD_BRIDGE_PYTHON_WRAPPER`. Never run a
  helper copy from a worktree and never a bare interpreter for a bridge tool.
- Never append to or edit the bridge log, a claim or a telemetry record by hand.

## Requests and replies

- Before answering, read the exact sender, task, timestamp, message and payload with the
  pinned reader (`Read-AgentBridge.ps1 -Raw -NoAckReceived -NoContinuity`).
- A request with a `request_id` starts with `Start-BridgeRequestTurn.ps1` and is answered
  with `Write-BridgeTaskReply.ps1`, bound to that exact request. Keep the request JSON in
  memory; never save it to a file. A later supplement is an unbound correction on the same
  task id.
- Immediately before every bound post, re-read the bridge. If a newer event addressed to
  you exists, read it first and rebuild the reply.
- A result another lane waits on is never marked informational. `finding` is a veto type
  for review identities; informational content uses `message`.

## Waking

- Waking is event-driven. Keep exactly one Monitor on the pinned `Monitor-AgentBridge.ps1`
  (`-Agent <id> -TargetedOnly -IncludeWakeRequests -Json -PollIntervalMs 1000`), and re-arm
  it only when it expires.
- An empty inbox consumes no model turn. Do not replace events with an idle timer or a
  scheduled self-wake.

## Models

- Start on the native/default model and effort that your launcher selects. Do not change
  the model or effort, buy capacity or bypass a provider limit.
- Provider capacity, quota and account identity stay unknown until observed; a reported
  effort level motivates placement but is never evidence of runtime model identity.
- A model or profile change happens only through the signed lane-profile process.

## Claims

- Claim before editing (`-Mode write` with an explicit write scope), one active claim per
  bounded slice. Refresh a long slice before its lease ends (`-Force -Mode write
  -LeaseSeconds <n>`); release with `Release-AgentTask.ps1`.
- Never edit a file another live claim or owner holds. Cross-owner wiring goes back to its
  owner as an explicit interface request.
- An expired claim never discards pushed code, reviews or evidence.

## Source and git

- Work only in persistent C-drive worktrees that share the repository history. Never in a
  RAM disk, a temporary or extracted folder, and never `git init`.
- Every change lands through a pull request: no direct push to `main`, no force-push, no
  `--admin`, no `--no-verify`. Commit through `tools/savepoint.ps1` with an explicit Bridge
  `-TestPath`, staging only owned files.
- After a push, confirm the remote tip with `git ls-remote` for up to 180 seconds before
  calling the push failed.

## Tests

- Reproduce an alleged bug before patching it; start new behavior with a focused failing
  test.
- Run only the affected tests, on isolated runtime roots with this lane's bridge identity
  variables removed, and never against a live root. Never run the full product suite unless
  a request assigns it.
- Report exact commands, results and log hashes. A test that was written but not run is
  reported as not run.

## Review independence

- The author of a change is never its reviewer. A review lane never passes its own
  implementation; the other review lane or an independent path reviews it.
- A formal review pass binds to one exact head (a top-level `head` field) and never carries
  forward to another head. A veto outranks a pass.

## Checkpoints

- After each bounded slice, update the lane checkpoint with the installed package's
  `Write-WdLaneCurrentState.ps1`, naming any alternate source worktree, branch and head in
  the evidence.
- Keep control words out of `next_action` and `blockers`; declare holds only with the paired
  `-WorkHeld` and `-ReleaseHeld` switches.

## Authority limits

- No deploy, install, tag, merge to `main`, scheduled-task change, lane stop or relaunch,
  runtime flip or activation unless a request grants exactly that step. Approvals are exact
  and never carry forward to another commit or manifest.
- Park a decision that belongs to the operator on the bridge with options and a
  recommendation, then continue with owned work.
- Report measured continuity only for the observed interval; never promise uninterrupted
  operation or a successful next turn.
