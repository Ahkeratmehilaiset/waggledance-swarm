# Exact unbound withdrawal in the next-action selector

Status: one contract for both next-action selectors. The Python selector is
`tools/bridge_next_action.py`. The PowerShell selector is
`.agent-bridge/bin/Get-BridgeNextAction.ps1` with
`.agent-bridge/bin/BridgeRequestContract.ps1`; it is changed in a separate
change and ships in the same bundle. Decision records: Lead byte-contract
decision `318100B1…` and case-parity disposition `97083993…`
(2026-10-07).

## Problem

A requester can withdraw a notice it sent earlier by posting a closure that
names the notice in a `withdraws` descriptor. The selector at `9645e93d` did
not read that descriptor, which caused two defects:

- **D1.** When a task has two request versions, the selector requires explicit
  correlation. The descriptor was not counted as correlation, so the withdrawn
  version stayed routed.
- **D2.** When a task has one version, generic same-task requester closure
  closed the notice even when the descriptor named a different row, for
  example a different `ts_utc`.

## Descriptor

The descriptor can sit at `payload.withdraws` or at a top-level `withdraws`.
If both are present they must be identical; otherwise it is malformed.

### Member spelling

Only the exact, lowercase spelling `withdraws` can carry a descriptor. Any
ASCII case variant (`Withdraws`, `WITHDRAWS`, `withDraws`, ...) at the top
level or in `payload` still marks the event as withdrawal-bearing:

- A variant spelling is malformed, even when its descriptor and digest are
  exact. It closes nothing and never falls back to generic closure.
- A variant in one location and the exact spelling in the other is
  malformed.
- Two spellings in one JSON object never reach the selector: the canonical
  reader rejects ASCII-case-colliding keys and the selection fails closed.
  A caller that passes such a mapping directly still gets `malformed`.
- A name that is not an ASCII case variant (for example `withdraw`,
  `withdrawn` or `withdraws_`) is not a withdraws member, and legacy rules
  apply. The fold is ASCII only. Non-ASCII look-alike letters, such as a
  dotless `ı` or a long `ſ`, do not fold. Both PowerShell hosts (5.1 and 7)
  also leave such keys unmatched, which was measured on 2026-10-07.

The same rule applies to the `payload` container (finding F17-1, disposition
`6B7E5B6D…`). Any ASCII case variant of `payload` (`Payload`, `PAYLOAD`, ...)
whose value is an object and holds any ASCII case variant of `withdraws`
marks the event as withdrawal-bearing:

- A withdraws member under a variant container is malformed, even when the
  member is spelled exactly and its digest is exact. It never falls back to
  generic closure.
- `payload` and a variant container in one event never reach the selector:
  the canonical reader rejects the row as ASCII-case-colliding. A caller that
  passes such a mapping directly gets `malformed` whenever a variant container
  holds a withdraws member.
- A variant container without a withdraws member, or whose value is not an
  object, adds nothing, and legacy rules apply.
- The canonical reader rejects non-ASCII property names, so a non-ASCII
  look-alike container never reaches the Python selector.

Descriptor field names, registry owner keys and the request and closure
fields that only the withdrawal path reads are compared ordinally, as
written.

```json
"withdraws": {
  "agent": "fable-5",
  "type": "message",
  "status": "fix_pushed",
  "task_id": "fable-5/example",
  "ts_utc": "2026-10-06T17:29:29.9136087Z",
  "raw_line_sha256": "<64 lowercase hex>"
}
```

All six fields are required strings. `ts_utc` must parse as a UTC timestamp.

## `raw_line_sha256` is a reader-row digest

The field keeps its historical name, but its value is **not** a physical-row
hash. It is the lowercase SHA-256 of the strict UTF-8 encoding of the row as
the canonical bridge reader yields it:

- the row is split on LF;
- at most one trailing CR is removed;
- every other byte is kept, including whitespace.

The selector never re-serializes JSON, trims, or decodes with ANSI to compute
it. It also never reads the digest from an event field. The reader-side
digest lives in a side table keyed by event identity, filled by
`_parse_selected_rows`. A copied plain list of events has no side table, and
every withdrawal on it is non-closing.

LF and CRLF terminators give the same digest. This terminator equivalence is
accepted only for closing an unbound notice, which also needs an exact
descriptor, registered identity, append order and uniqueness. It does not
apply to queue, WAL, replay or authority proofs.

The historical writer (fable-5, pwsh 7, 2026-10-06) hashed a
`Get-Content` line. For rows without an interior CR it gives the same value as
the reader-row digest. Windows PowerShell 5.1 `Get-Content` decodes non-ASCII
text with the ANSI code page and gives a different value. Such historical
hashes stay non-closing and are not repaired. Whether the 2026-10-06 target
row actually matches has not been verified until a trusted selector observes
it.

## Rules

A closure event that carries a `withdraws` member (in any ASCII case
spelling) is handled only by
`withdrawal_target` or by the full bound reply contract. It never takes part
in generic same-task, requester-terminal or PR-key closure, including the
same-task closure of direct RCO pass/block requests and idle-protocol
progress (`responds_to` a proposal id). Idle-protocol progress events without
a `withdraws` member are unchanged.

The withdrawal closes exactly one request version, and only when all of the
following hold:

1. The request is unbound: it has no `request_id`, nonce, token, task
   revision or expected responders. Bound requests close only through their
   reply contract, which is unchanged. The request is also not a control
   signal.
2. The closure is an explicit requester closure by the request's own agent on
   the same `task_id`. Both are compared exactly, with no case folding.
3. The descriptor's `agent`, `type`, `status`, `task_id` and `ts_utc` equal
   the request's values exactly as written.
   - `ts_utc` is compared as a string. Python datetimes keep only
     microseconds, while the PowerShell reader keeps 100 ns ticks, so a parsed
     comparison could make the two selectors disagree.
4. The closure comes after the request in reader append order.
5. The request and the closure each match their entry in
   `configs/bridge_identity_registry.json` (status `valid`). A missing,
   foreign or unregistered `agent_uuid`, or a missing, malformed or
   unreadable registry (including an OS read error such as access denied),
   is `identity_unverified`. Only the withdrawal path absorbs these errors;
   other registry users keep their own behaviour.
6. The request's reader-row digest exists, is addressable, equals
   `raw_line_sha256`, and names exactly one row in the reader window.

## Outcomes

| Outcome | Effect | Diagnostic |
|---|---|---|
| `exact` | closes that one version, also when the task has several versions | — |
| `mismatch` | closes nothing | — |
| `malformed` (bad shape, `null`, top-level/payload conflict, case-variant or ambiguous member or container spelling) | closes nothing | `malformed_withdrawal` |
| `identity_unverified` | closes nothing | `withdrawal_identity_unverified` |
| `unverifiable` (no reader side table) | closes nothing | `withdrawal_unverifiable` |
| `non_addressable` (row still has a CR, or the historical bare-CR split row) | closes nothing | `withdrawal_non_addressable` |
| `duplicate` (more than one row with the same digest) | closes nothing | `withdrawal_duplicate` |

Diagnostics appear in the report as `withdrawal_diagnostics`, and only when
the list is non-empty. They list closures that were not applied. A diagnostic
never claims that a request is absent or that coverage is complete.

## Unchanged behaviour

- Events without a `withdraws` member keep today's behaviour. This covers the
  legacy requester closure, `request_ts_utc` correlation, control signals,
  late bound answers and bound cancellation.
- `reply_matches_request` and the bound reply contract are not modified.
- Invalid UTF-8 or a malformed row in the reader window still makes the
  selector fail closed, as before.
- Control signals (`changes_requested`, `rco_fail`, `review_failed`,
  `blocked` decisions and findings) are never closed by a withdrawal. They
  keep their explicit-correlation rule and stay routed otherwise.

## Tests

`tests/tools/test_bridge_exact_withdrawal.py` covers:

- D1, its two-version twin, and D2;
- LF, CRLF and trailing-whitespace rows, plus hashes taken with the CR or
  with whitespace trimmed;
- non-ASCII text, an interior bare CR, a double CR, and the historical
  bare-CR split row;
- a mismatch in each field on its own (hash, agent, type, status, task, ts,
  case);
- a non-owner closure, append order, and a non-terminal closure status;
- missing or foreign UUIDs on the request or closure, and a missing or
  unreadable registry;
- duplicate rows, and a missing or copied side table;
- malformed descriptors, and a conflict between top-level and payload
  descriptors;
- case-variant member spellings at the top level and in payload, each with an
  exact and a wrong digest, plus ambiguous spellings, exactly spelled
  positives and near-miss names that keep legacy rules;
- case-variant `payload` containers with exact and variant members, exact and
  wrong digests and `withdrawn`/`closed` closures; a lowercase exact
  positive; variant containers without a member, not an object or `null`;
  `payload` plus a variant container; a top-level member plus a variant
  container; a non-ASCII look-alike container;
- variant-container members on idle-protocol progress, target answers,
  control signals, `request_id` requests and full bound replies;
- case-variant members on target answers, control signals, `request_id`
  requests, full bound replies, direct RCO pass requests and idle-protocol
  progress;
- bound requests, and the legacy controls.
