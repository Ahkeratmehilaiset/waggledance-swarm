# WaggleDance parallel execution policy v1

This policy changes scheduling discipline, not authority. The live bridge,
current claims, exact Git heads, and explicit role permissions remain binding.

## Reboot continuation

Each lane keeps one compact checkpoint at
`<worktree>\.codex-audit\wd-current-state.json` using
`C:\Python\Write-WdLaneCurrentState.ps1`. Update it after every bounded slice
and before any planned stop. It contains the task, exact branch/HEAD, write
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

Each Claude lane maintains exactly one lane-specific durable five-minute cron
backstop with `CronList`/`CronCreate` and removes duplicates with `CronDelete`.
The cron prompt tells that lane to read its compact state and bridge next action
and execute one eligible bounded slice. Dynamic `/loop` turns still call
`ScheduleWakeup` every turn. The durable cron is the recovery backstop when one
dynamic wakeup is missed; it is not permission to duplicate a live claim.

Durable Claude cron jobs expire after seven days, so each lane verifies and
refreshes its one exact job during normal daily work and after reboot.
