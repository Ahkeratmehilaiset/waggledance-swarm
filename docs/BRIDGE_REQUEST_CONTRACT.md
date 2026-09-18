# Bridge requests, local claims and workflow measurements

## Request and reply contract

New requests written by the bundled `Write-AgentEvent.ps1` receive an immutable
`request_id` and `request_digest`. A new revision gets a new ID even when its
`task_id` stays the same. An identical transport retry keeps the ID **and the
full original content/identity**. Conflicting reuse remains visibly open.

Read the full request using the pinned reader; next-action messages are summaries.
Retain the returned object as `$request`, then:

```powershell
& "$env:WD_BRIDGE_BIN\Start-BridgeRequestTurn.ps1" -Agent $env:AGENT_BRIDGE_AGENT `
  -RequestEventJson ($request | ConvertTo-Json -Depth 32 -Compress)
& "$env:WD_BRIDGE_BIN\Write-AgentEvent.ps1" -Agent $env:AGENT_BRIDGE_AGENT `
  -Type message -Status answered -TaskId $request.task_id -To $request.agent `
  -ReplyToEventJson ($request | ConvertTo-Json -Depth 32 -Compress) `
  -Message 'Result and verification' -PayloadJson '{"result":{"cost":45}}'
```

Use the lane's actual UUID/session/run metadata, inherited from its launch.
The writer copies `in_reply_to_request_id`, the request digest and the requester
identity. It rejects contradictory responder identity or correlation fields.
For the five fleet lanes, missing identity snapshots remain unresolved and are
reported in the delivery receipt; a missing target cannot close the request.
Known targets in a multi-target request keep their identity checks even when
another target is missing. Verify that missing lane and issue a new request ID.
Unregistered legacy recipients without any identity snapshots retain legacy
identity handling; do not use those recipients for a fleet completion gate.
Readers require that binding before treating an answer as completion. A received
ACK, a wake notification and a successful queue command are not completion.
Legacy nonce/revision/timestamp bindings remain enforced. An ambiguous bare
legacy request is left open rather than guessing which revision was answered.
Unversioned single legacy requests retain their old behavior.

Routers distinguish `open_incoming_event_count` (raw rows),
`open_incoming_count` (deduplicated requests), and `open_incoming_task_count`
(distinct task IDs). Two request IDs on one task are two requests, not one.

## Resource scopes

| Scope | Meaning |
| --- | --- |
| `.codex-audit/wd-current-state.json` | Physical checkpoint in the claim's recorded cwd |
| `worktree:.codex-audit/path` | Physical local audit resource under that cwd |
| `shared:shared/path` | Physical resource under the shared bridge root |
| `repo:src/module.py` or `src/module.py` | Logical source resource across all worktrees |

Parent paths cover children. Source claims continue to conflict across different
worktrees. Missing legacy cwd stays conservative. Traversal, unknown resource
kinds, links/junctions and ambiguous Windows aliases are rejected, including
when they appear in an existing foreign claim. No claim is released implicitly.

## Role-specific preparation and handoffs

The packaged read-only `tools/bridge_workflow.py prepare --input plan.json`
requires an existing `authorization_ref`, exact requester/responder identities,
task/revision, instruction, `consumes_fields`, `result_fields`, and role data.
It prepares JSON only. Lead verifies the authorization and current identities
before sending with the pinned writer, retaining the **actual durable receipt**.
Use the prepared `request_id` as `-RequestId` and its payload as `-PayloadJson`.

Allowed inputs are Tools `inputs/checks/handoff`, RCO `evidence/acceptance_criteria/cases`,
and Fable `requirements/constraints/handoff_target`. Other-role data is omitted.
Example plan:

```json
{
  "role": "planner", "target": "fable-5",
  "authorization_ref": "operator/authorized-task", "task_id": "example/plan",
  "revision": "v1", "instruction": "Prepare the requested cost plan",
  "requester": {"agent":"codex-lead-1","agent_uuid":"LIVE_UUID","session_id":"LIVE_SESSION","run_id":"LIVE_RUN"},
  "responder": {"agent":"fable-5","agent_uuid":"LIVE_UUID","session_id":"LIVE_SESSION","run_id":"LIVE_RUN"},
  "consumes_fields": ["requirements"], "result_fields": ["cost"],
  "data": {"requirements": [10,15,20]}
}
```

`handoff --input handoff.json` takes `{request, reply, next_plan}`, validates the
exact binding and result fields, and prepares the authorized next request with
the original result embedded. It cannot change the authorization reference.
Lead can prepare the next plan before the upstream answer arrives and run this
check immediately afterwards. It does not create a daemon or grant authority.
Grok continues through the existing hourly-budgeted helper, never this queue.

## Health and latency

The five continuous workers are Lead, Tools, RCO1, RCO2 and Fable. Grok is
on-demand; quiet Grok and historical helper identities are not stalled workers.
Open-request age is reported separately from activity/heartbeat age. An explicit
suppression may still appear as a non-actionable historical diagnostic.

Local observations under `shared/telemetry/stage-*.json` capture canonical
request persistence, watcher detection, Tools queue acceptance, explicit model
turn start and substantive answer persistence. Tools passes the actual
`delivery_id` to the turn-start helper; this joins its queue observation to the
request. A Claude Monitor print is not a model turn or queue acceptance, so its
unobserved relay stage stays unknown.
The turn-start marker is the agent's first explicit tool observation; the model
may have started reasoning earlier. Durations between these observations are
not engine timings and must not be described as pure scheduling or thinking time.

```powershell
& $env:WD_BRIDGE_PYTHON_WRAPPER tools/bridge_workflow.py latency `
  --input request-and-target.json --telemetry-directory "$env:AGENT_BRIDGE_RUNTIME_ROOT\shared\telemetry"
```

The input is `{ "request": <full request>, "target": "codex-tools-1" }`.
Missing stages are null, negative intervals are flagged, and no stage or duration
proves task completion. Telemetry is local observation, not authenticated evidence.
Telemetry failure does not turn a successful canonical append into a retry.

## Release acceptance

Keep native Lead/Tools conversations, worktrees, pending claims and merge HOLDs.
Stage the exact reviewed commit and verify hashes. All active lanes must adopt
the new pinned helpers and reply instructions together; an old process does not
adopt changed scripts merely because the stable launcher changed.

Before declaring the release ready, run the router/view, resource and relay crash
regressions; require green full CI and exact-head reviews. Install the same bundle,
verify scheduler/watchers, and run live requests plus a revised same-task request
and a validated handoff. Preserve unknown effects across interrupted relays.
Run `start-wd-all.ps1 -Auto` acceptance separately from `-DryRun`; neither a
simulation nor a dry run proves an actual restore or physical reboot.

Fleet discovery parses the actual PowerShell script and argument vector using
the supervisor's parser. Script names inside a test's `-Command` payload do not
identify a live lane. Missing process metadata is rechecked once: an exited
process is harmless, while an unreadable live process or reused PID still blocks.

The existing hashed, expiring external-session snapshot can also explicitly
attribute a continuously spawning outside worker with `kind: native_parent`.
That record uses the same exact `pid`, `name`, `process_start_utc`,
`executable_path` and `command_line` fields for the parent, plus
`native_child_name`, `native_child_executable_path` and a nonempty,
whitespace-terminated `native_child_command_prefix`. Only direct children born
after that exact parent, with that exact executable and command prefix, qualify.
Managed lane ancestry takes precedence; this never adopts, stops or grants
authority to an external process. A snapshot does not approve future parent
lifetimes and still expires within 24 hours. Ordinary native session records
remain bound to their individual process lifetime.
