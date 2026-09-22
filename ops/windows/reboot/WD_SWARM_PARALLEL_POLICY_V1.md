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

After every restart, native session-only cron is mechanically disabled by the
manifest-pinned `wd-claude-event-driven-settings.json` passed with `--settings`.
`CLAUDE_CODE_DISABLE_CRON=1` stops existing schedules and disables `CronList`
and `CronCreate`; do not call unavailable tools. Existing job/history records
are preserved. Empty-queue checks belong to ordinary code in the targeted
Monitor. Do not replace disabled cron with another idle timer. A dynamic timed
wake must name actual pending work or a known capacity reset and end when that
reason ends. Real future work can use a bounded dynamic one-shot; native cron
is not the transport for bridge requests, responses or corrections.

Every cron, Monitor, and dynamic wake checks new addressed requests **before**
deciding no-op. A future one-shot never covers unread incoming work. Keep one
native `Monitor` tool attached to the pinned `Monitor-AgentBridge.ps1 -Agent
<lane> -TargetedOnly -IncludeWakeRequests -Json -PollIntervalMs 1000`.
Start that monitor before the initial inbox read, and recover/report monitor
exit without substituting periodic model polling. A detached shell process by itself
does not deliver a turn to the model. Pending idle timers cannot delay a request.

Retrieve full request message and payload with the pinned `Read-AgentBridge.ps1
-Agent <lane> -Raw -NoAckReceived -NoContinuity -Tail 1200`, matching exact sender,
task and timestamp. Routing summaries are incomplete. Do not replace this reader
with direct log reads. Distinguish `routing_match` (request-id lookup) from
`binding_valid` (the full pinned binding predicate, including nonce, digest and
expected responder), then `schema_valid` and `semantic_valid`. A wrong nonce
fails binding even if the request id matches. Report validation failures as blockers. Increase the
bounded tail or use `-Tail 0` when the exact request is older.

Keep a dynamic wake only for actual pending work, with its confirmed absolute deadline recorded.
On a no-op Monitor or dynamic-loop turn, use the recorded scheduler receipt to retain an
already-pending one-shot; do not call `ScheduleWakeup` just to end the turn.
Create a new one-shot only when none is pending or a real scheduling change
requires it. Relative-delay rearming can round the target to a later minute
even when remaining-time arithmetic is used. Read the clock immediately before
an intentional rearm and record the confirmed target returned by the scheduler
not a placeholder estimate. When the deadline is due, resume the
bounded turn now if the named work is still eligible. With no pending work,
finish with the Monitor attached and no idle timer. A timer never grants
permission to duplicate a live claim.

The current launcher's `turn_mode` scheduling instruction supersedes only
legacy self-pacing sections of external role prompts, including durable-cron
claims, mandatory rearming every turn, and fixed-delay idle loops. Role identity,
write scope, task ownership, review and promotion permissions are unchanged.

An explicitly managed lane instead uses its launcher-owned turn loop and must
not create native cron or `ScheduleWakeup` jobs alongside it. Selecting managed
mode applies only to new launches; it does not resume an existing interactive
CLI session. Native dynamic firing, cron firing, and managed turn completion
are separate evidence and must not be reported interchangeably.

## Capacity and request preflight

Use the single installed reader for capacity:

    powershell -NoProfile -NonInteractive -File C:\Python\Get-WdCapacityStatus.ps1 -Summary

Add -Agent fable-5 -Json for a compact machine-readable lane view; omit
-Summary and -Agent for full observations. This does not collect provider
data. An observed native process is not an authenticated quota-pool binding.
Keep authentication history, quota, activity, observation freshness and
next-turn readiness separate. General Claude percentages do not establish
remaining Fable-specific allowance. A rate-limit error is a blocker to
reconcile, not permission to switch accounts, buy credits, release claims
or repeatedly retry.

Lead uses capacity to choose among existing eligible workers, preserving the
original task, request revision, checkpoint, write scope and review requirements.
Before a handoff, reconcile the old owner's claim and any pending side effects;
create a new request bound to the actual recipient/session, retaining an explicit
reference to the original request. An old request's expected responder must not
be edited or impersonated after a restart. A blocked RCO review stays required.

Treat `shared_or_unknown_<provider>` as a conservative accounting group, not a
verified pool id. Never add the apparent headroom of Lead and Tools together,
or of the three Claude lanes. Native Codex quota metadata binds to a specific
conversation and observation time; it does not authenticate an account/pool.
Stale evidence and unknown account identity cannot authorize automatic model
switching. The advisor may propose a profile only after its explicit policy,
role qualification, catalog, quota binding and safe-boundary checks pass.

A Claude **weekly/session** usage limit is shared across models. Switching
Sonnet to Opus (or back) does not free that allowance. A model-family limit is
different, but a replacement still needs verified usable capacity. Preserve
the conversation and work while waiting; use the native free wait-until-reset
option when its reset is shown. A reset timestamp permits one capacity recheck,
not a readiness promise. Never enable usage credits automatically.

References: https://code.claude.com/docs/en/costs and
https://learn.chatgpt.com/docs/app-server (checked 2026-09-21).

New structured requests must include an explicit
result_contract.schema=wd.task-result-contract.v1 and nonempty required
array. The workflow preparer supplies it; the writer rejects malformed
contracts and orphan result_fields before writing. Historical requests
without a contract remain readable with schema validation unknown.
Correlation, result structure and independent content review remain distinct.
# Native quota failures and scheduled retries

The installed event-driven policy uses `Install-WdClaudeCapacityHooks.ps1
-EnableBridgeAlerts -DisableNativeCron -Agent <exact-Claude-lane>`. It writes a
lane-local cron disable value and replaces only its previously owned hooks.
It does not install automatic cron resume. A successful turn must not restore
idle polling. The command-line settings layer preserves this choice even if
an older local quota guard had left a `0`. Other agents/settings are unaffected.
This does not remove a provider quota or prove next-turn availability.

The optional lane-local `Install-WdClaudeCapacityHooks.ps1 -EnableBridgeAlerts`
integration sends a sanitized native failure notice to Lead without a model turn.
Delivery uses a durable local intent and canonical receipt reconciliation. It is
at-most-once, best effort: an uncertain or lost write requires reconciliation;
this is not a guarantee that Lead has received or handled the notice.

`-PauseNativeCronOnLimit -Agent <exact-Claude-lane>` additionally opts into a
native cron guard. It requires verified bridge helper and native-session identity.
On `rate_limit`, it owns only a newly created lane-local
`env.CLAUDE_CODE_DISABLE_CRON=1` override. Existing overrides are preserved.
Only a successful native `Stop` in the same session changes that owned override
to explicit `0`, retaining its ownership record for subsequent pause cycles.
Deleting the key alone does not reset the running CLI's imported environment.
The guard may reuse this `0` only with the same native identity and an exact
settings hash; it never adopts a pre-existing user override. Successful Stop
releases the pause;
statusline callbacks, a model-name change, time passing, and a failed turn do not.
Other settings and the native conversation remain intact. Conflicting edits or
interrupted updates require explicit reconciliation. This option remains off by
default and requires an installed-CLI pause-and-resume acceptance test before use.
Changing the setting is not itself proof that native scheduling resumed.

The guard does not change models, buy credits, release claims, or grant reviewer
authority. It cannot predict a model-specific limit missing from provider
metadata. A blocked lane keeps its pending work; Lead may coordinate a suitable
existing worker within the operator's existing scope. If no approved capacity is
available, report the blocker and wait rather than claim uninterrupted progress.
