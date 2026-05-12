# ADR 020: Bridge Type Field Is Non-Gating

Date: 2026-05-11

Status: Accepted for EIG2-M0 (Claude peer-review signed 2026-05-11)

Author: Codex

Peer reviewer: Claude

## Context

During the EIG2-M0 ownership split, Claude answered Codex with a bridge event
whose `type` was `ownership_proposal` and whose `status` was `open`. The
payload was a substantive reply to task
`eig2-m0-ownership-split-2026-05-11`, but the bridge continuity tooling still
reported Codex's original request as waiting.

The live failure was not a missing event. The event existed in
`.agent-bridge/shared/events.jsonl`. The failure was that readers treated a
narrow type/status allow-list as the source of truth for whether an event could
answer a task. This is exactly the bridge-schema divergence risk identified in
EIG2 R3: EIG2 will project richer views over the existing bridge stream, and
those projections must not lose events.

The immediate tooling fix landed in:

- PR #264 for `waggledance/r22-16-changelog-audit-fix-series`
- PR #265 for `main`

## Decision

The bridge `type` field is semantic metadata. It is not a gate for continuity
or reply detection.

For a given `task_id`, readers must scan all later events from the target agent.
An event is a substantive answer unless it is explicitly one of these
non-answer classes:

- ACK-only: `message/received`, `seen`, `acknowledged`
- Infrastructure-only: `heartbeat`, `liveness`, `wake_request`

Directed custom events are allowed. Examples include `ownership_proposal`,
`synthesis`, `simulation_open`, and `sandbox_drop`. A reader may display these
types differently, but it must not drop them from task continuity.

Directed `to` values are split per target. A bridge event with
`to: "claude,operator"` creates separate continuity state for `claude` and
`operator`; it does not create a synthetic target named `claude,operator`.

## Rules

1. Do not filter bridge replies by `type == "message"`.
2. Do not require custom event types to be added to every reader before they
   become visible.
3. Keep `message/received` as ACK-only. It proves the target saw the request;
   it never closes the request.
4. Treat `message/answered`, `message/answered_plus_reminder`, and
   `message/answered_after_recovery` as substantive message replies. New
   answer-like message statuses must be added to the classifier and covered
   by a bridge continuity smoke test before use.
5. Treat custom targeted events with open/proposal/request-style statuses as
   request-like for the target agent.
6. Keep bridge schema migration adapter-first. EIG2 projections may add
   fields such as `protocol_version`, `parent_id`, and `payload_hash`, but the
   live `.agent-bridge` JSONL schema remains readable by existing tools.

## Consequences

Positive:

- Agent-to-agent consensus no longer stalls when one agent uses a richer event
  type for a substantive reply.
- EIG2 bridge adapters can be additive and projection-based instead of forcing
  a live schema migration.
- Multi-target messages become queryable per agent.

Tradeoffs:

- Readers must share a continuity classifier rather than each keeping a local
  allow-list.
- Custom event types can create more visible open work. That is intentional:
  an addressed custom event with an open/proposal status is work until an agent
  answers it or the requester closes it.

## Validation

Implemented by `BridgeEventClassifier.ps1`, used by:

- `.agent-bridge/bin/Read-AgentBridge.ps1`
- `.agent-bridge/bin/Get-AgentBridgeStatus.ps1`
- `.agent-bridge/bin/Get-BridgeNextAction.ps1`

Regression coverage:

- `.agent-bridge/bin/Test-BridgePolymorphicContinuitySmoke.ps1`

The smoke test reproduces the live `ownership_proposal/open` case and asserts:

- custom `ownership_proposal/open` counts as an answer to the original request;
- `message/answered_plus_reminder` counts as a substantive message answer;
- `Read-AgentBridge.ps1` displays the custom reply in outgoing continuity;
- `Get-BridgeNextAction.ps1` does not ask Codex to answer an already answered
  post-chat request.

PR #265 GitHub checks passed on `main`: `security-scan`, `test (3.11)`,
`test (3.12)`, `test (3.13)`, and `unified`.
