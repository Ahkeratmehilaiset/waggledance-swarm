# R16 proposal — convert architect/security/reliability synthesis from theater to genuine

- timestamp: 2026-05-09T15:35:00Z
- branch: waggledance/r15-stale-claim-lease (proposal lives in R15 PR per
  operator amendment 2026-05-09T~14:50Z; implementation deferred to R16)
- author: Claude Opus 4.7

## What's "theatrical" today

When Codex does a pre-merge review, they emit a single bridge
`message/answered` event whose body has three labelled paragraphs:

```
Architect: ...
Security: ...
Reliability: ...
```

The **output structure** has three roles. The **invocation
structure** has one Codex pass. There is no separate context, no
separate prompt, no role-specific evidence-bundle separation. Drift
is one tired Codex turn away — three labels can collapse into one
generic perspective without any tooling-level signal that the
review degraded.

`orchestrator/Invoke-WaggleReview.ps1` already accepts
`-Role architect|security|reliability` as a Mandatory parameter,
and `orchestrator/lib/external_review/EpochCycleTrigger.ps1`
consumes `internal_review_verdicts.{architect,security,reliability}`
as separate fields. The plumbing exists; the bridge-loop just
doesn't use it.

## R16 proposal

Add a new wrapper, e.g.:

```
.agent-bridge/bin/Invoke-RoleReview.ps1 \
    -Agent codex \
    -Target <pr-number-or-branch-or-iteration-id> \
    -Roles architect,security,reliability \
    [-Synthesis on|off]
```

Behavior:

1. **Three independent role passes.** For each role in `-Roles`,
   spawn a separate `Invoke-WaggleReview.ps1 -Role <role>` (or
   equivalent) with role-specific:
   - prompt preamble (the role's responsibility statement);
   - evidence bundle filter (e.g. `architect` sees the diff +
     PR description + adjacent files; `security` sees the diff +
     `grep`-extracted input-surface lines; `reliability` sees the
     diff + the existing test suite touching changed lines);
   - structured-output schema (each role emits a verdict +
     numbered findings, NOT free-text labelled paragraphs).

2. **Synthesis pass on top.** If `-Synthesis on` (default), a
   final fourth pass takes the three structured outputs and emits
   one combined `message/answered` event with:
   - per-role verdict (approve / approve_with_conditions /
     finding_open / block);
   - per-role finding count + max severity;
   - synthesized short rationale that flags conflicts between
     roles (e.g. "architect approves; security blocks on input
     validation gap" — currently impossible because the three
     never disagree visibly).

3. **Bridge integration.** Each role's verdict gets emitted as a
   separate bridge event with a sub-task_id:
   ```
   <task>-architect-2026-05-09
   <task>-security-2026-05-09
   <task>-reliability-2026-05-09
   ```
   so `Read-AgentBridge.ps1 -ShowContinuity` can show three
   parallel verdict tracks instead of one collapsed one. The
   synthesis event references all three sub-task_ids in its
   payload.

4. **Schema reuse.** Use the schema
   `internal_review_verdicts.{architect,security,reliability}`
   already consumed by `EpochCycleTrigger.ps1` so the new wrapper
   feeds existing escalation rules without further plumbing.

## Why now (and not in R15)

- R15 (stale claim lease) is the missing operational gate.
  Once stale-test passes, agents won't accidentally hold each
  other's claims; that's a precondition for true parallel role
  invocation.
- The synthesis-as-theater was tolerable while the bridge was the
  bottleneck. With the bridge solid, the next-level fragility is
  review depth.
- Operator's accepted plan (R14 amendment 2026-05-09) explicitly
  scopes this as R16, not R15. This file is the proposed
  R16 spec.

## Smallest-meaningful scope for R16

When R16 lands, it should be ONE PR adding:
- `.agent-bridge/bin/Invoke-RoleReview.ps1` (the wrapper).
- One smoke test that runs all three roles in dry-run mode
  against a fixed canned diff and verifies three separate verdict
  events plus one synthesis event.
- Doc updates in `BRIDGE_PROTOCOL.md` rule 7
  ("Alternate review loops") to require `Invoke-RoleReview.ps1`
  for any bridge-protocol or core-runtime change, and to mark
  the legacy "three labels in one Codex paragraph" pattern as
  deprecated.

R16 should NOT yet:
- replace orchestrator's existing `Invoke-WaggleReview.ps1`
  (the wrapper calls it, doesn't replace it);
- add new external-review providers (those stay where they are);
- enforce the wrapper at PR-merge time (that's a separate
  governance question).

## Measurable success criteria

A genuine-role review must satisfy:
- **Triple-output**: three independent verdict events emitted to
  the bridge, each with non-zero unique findings text.
- **Disagreement visibility**: in synthetic-conflict test (an
  architect-pass / security-block diff), the synthesis surfaces
  the disagreement explicitly; a single-Codex-pass would
  typically smooth it over.
- **Operator-readable**: the synthesis event must contain a
  short summary that names the disagreement if any (no buried
  blocks).

Until the synthetic-conflict test passes, the role review remains
nominally three-role but operationally one — i.e. theater.
