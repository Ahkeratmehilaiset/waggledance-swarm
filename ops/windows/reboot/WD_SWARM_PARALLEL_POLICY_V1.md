# WaggleDance parallel execution policy v1

This policy changes scheduling discipline, not authority. The live bridge,
current claims, exact Git heads, and explicit role permissions remain binding.

## Reboot continuation

Each lane keeps its canonical compact checkpoint at
`<fleet-manifest lane worktree>\.codex-audit\wd-current-state.json` using
`C:\Python\Write-WdLaneCurrentState.ps1 -Worktree <fleet-manifest lane worktree>`.
Resolve that worktree from the current reboot pointer's fleet manifest, not
from the shell's current directory. Update it after every bounded slice,
after re-reading current Git/bridge state at each wake, and before any planned
stop. Optional task-worktree checkpoints do not replace the canonical one.
For work in an alternate worktree, record its exact path, branch, commit and
next action in the checkpoint's evidence fields; retain the canonical
worktree's own derived branch/HEAD without substituting the task commit.
The checkpoint contains the task, exact branch/HEAD, write
scope, dirty paths, tests, bridge evidence, blockers, and the next executable
action. The bridge is authoritative for newer events. Large Markdown handoffs
are audit history and fallback only.

After an abrupt restart, read in this order:

1. the current reboot pointer;
2. the compact lane checkpoint, if valid;
3. current bridge next-action and claims without acknowledging stale traffic;
4. fleet roles and the lane role prompt;
5. Markdown handoffs only when the compact state is absent, inconsistent, or
   insufficient for a named historical fact.

Never recover uncommitted bytes by guessing. Git savepoints and written files
are durable; model memory is not.

## Parallel scheduler

The Lead keeps a ready queue with at least one file-disjoint, unblocked slice
for every available lane. A claim must name one exact task id, base/head, and
write scope before edits begin. Prefer these independent axes:

- Lead: core implementation and integration;
- Tools: tests, tooling, diagnostics, and documentation;
- RCO1: primary correctness/security review at an exact head;
- RCO2: independent adversarial and failure-mode review at the same exact head;
- Fable: a separate producer slice with a disjoint write scope.

Do not serialize unrelated axes behind one PR. Do serialize edits to the same
file or stateful resource, promotions, merges, deploys, and reviews that depend
on a new exact head. A blocked lane immediately publishes the blocker and
claims another eligible ready slice instead of silently waiting.

Tools remains one bridge identity and one parent consumer. Inside a bounded
Tools tick it may parallelize read-only discovery, exact-head checks, or
file-disjoint test processes. Child workers never claim bridge work, emit as
`codex-tools-1`, or edit the same write scope; the parent owns all bridge writes
and integrates results.

## Evidence reuse

Test and review evidence is reusable only when all of these match exactly:

- commit SHA;
- relevant file set and configuration;
- command and material environment inputs;
- evidence type and role.

Record the evidence id and exact SHA in bridge payloads and compact state.
Reuse avoids duplicate work; it never converts a deferred required review into
a waived review and never grants merge, deploy, or signature authority.

## Claude wake backstop

Each interactive Claude lane maintains exactly one lane-specific session-only
five-minute cron backstop with `CronList`/`CronCreate` and removes duplicates
with `CronDelete`. Recreate it after every restart; the installed build does not
persist these jobs across sessions. The cron prompt tells that lane to read its
compact state and bridge next action and execute one eligible bounded slice.

Every cron, Monitor, and dynamic wake checks new addressed requests **before**
deciding no-op. A future one-shot never covers unread incoming work. Keep one
native `Monitor` tool attached to the pinned `Monitor-AgentBridge.ps1 -Agent
<lane> -TargetedOnly -IncludeWakeRequests -Json -PollIntervalMs 1000`.
Start that monitor before the initial inbox read, and recover/report monitor
exit without disabling the cron inbox check. A detached shell process by itself
does not deliver a turn to the model. Pending idle timers cannot delay a request.

Retrieve full request message and payload with the pinned `Read-AgentBridge.ps1
-Agent <lane> -Raw -NoAckReceived -NoContinuity -Tail 1200`, matching exact sender,
task and timestamp. Routing summaries are incomplete. Do not replace this reader
with direct log reads. Report validation failures as blockers. Increase the
bounded tail or use `-Tail 0` when the exact request is older.

Keep one current dynamic wake with its confirmed absolute deadline recorded.
On a no-op cron, Monitor or dynamic-loop turn, use `CronList` to retain an
already-pending one-shot; do not call `ScheduleWakeup` just to end the turn.
Create a new one-shot only when none is pending or a real scheduling change
requires it. Relative-delay rearming can round the target to a later minute
even when remaining-time arithmetic is used. Read the clock immediately before
an intentional rearm and record the confirmed target returned by the scheduler
or `CronList`, not a placeholder estimate. When the deadline is due, resume the
bounded turn now. The cron is a missed-wakeup backstop, not permission to
duplicate a live claim.

The current launcher's `turn_mode` scheduling instruction supersedes only
legacy self-pacing sections of external role prompts, including durable-cron
claims, mandatory rearming every turn, and fixed-delay idle loops. Role identity,
write scope, task ownership, review and promotion permissions are unchanged.

An explicitly managed lane instead uses its launcher-owned turn loop and must
not create native cron or `ScheduleWakeup` jobs alongside it. Selecting managed
mode applies only to new launches; it does not resume an existing interactive
CLI session. Native dynamic firing, cron firing, and managed turn completion
are separate evidence and must not be reported interchangeably.
