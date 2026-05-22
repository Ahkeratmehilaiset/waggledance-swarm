# Operator Decision Pack v1

A **decision pack** is a machine-readable artifact an agent emits for a single
charter-gated operator decision — the escalation categories that the autonomous
loop must **never** auto-resolve (credentials, destructive git, payment,
write-scope conflict, legal/security, dependency security, Docker promotion).

The pack packages the options + concrete data + an agent recommendation and a
single operator sign-off field, so the operator clears the gate in **one step**
instead of a multi-round bridge discussion. A signed pack becomes normal
implementation input — it is **never** a merge bypass.

Packs live in `docs/operator_inbox/<decision-id>.yaml`. They are read/validated
by `tools/operator_decision_pack.py` and surfaced (open vs signed) by
`tools/bridge_loop_tick.py`. Neither tool ever resolves or mutates a pack.

## Schema (`waggledance.operator_decision_pack.v1`)

```yaml
schema_version: waggledance.operator_decision_pack.v1
decision_id: <kebab-case-id>            # unique; matches the file stem
category: <escalation-category>          # credentials | destructive_git | payment |
                                         # write_scope_conflict | legal_security |
                                         # dependency_security | docker_promotion
created_utc: <ISO-8601 Z>
author_agent: <claude|codex>
options:                                 # >= 2, each id unique
  - id: <option-id>
    summary: <one line>
    data: { ... }                        # concrete measured facts for this option
    agent_recommendation: <true|false>
  - id: <option-id>
    ...
operator_signoff:                        # EMPTY in draft (gate not cleared)
  signed_by: ""                          # operator fills: "operator:<id>:<ISO-8601 Z>"
  chosen_option: ""                      # operator fills: one of the option ids
structural_invariants:                   # booleans the agent must NOT flip
  no_main_branch_auto_merge: true
```

## Lifecycle

1. An agent detects a charter-gated decision, gathers concrete data, and writes
   a **draft** pack (empty `operator_signoff`).
2. `bridge_loop_tick` lists the pack under `open_operator_packs` and the agent
   emits one bridge event `type=decision status=operator_signoff_requested
   to=operator`.
3. The operator clears the gate in one step: set `signed_by` (`operator:<id>:<ts>`)
   and `chosen_option`.
4. The signed pack is now input to normal (PR-only, peer-reviewed) implementation
   work. The loop never acts on a pack itself.

## Fail-closed rules

- A pack is **signed only** when `signed_by` AND `chosen_option` (a valid option
  id) are both non-empty. Anything else is `open` (gate not cleared).
- A pack that does not parse / violates the schema is reported under `invalid`
  and surfaced for attention — never silently treated as signed.
