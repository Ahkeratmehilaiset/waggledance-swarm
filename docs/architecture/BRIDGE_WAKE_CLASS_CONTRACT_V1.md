# Bridge wake-class contract v1 (`wd.wake-class.v1`)

Status: W0 building block (Bridge v2 single-release plan), **pre-acceptance
candidate**. Revision 2 (`d08c7650`) replaced candidate `bbc54f90`, which
RCO1 review f8fc8be4 and RCO2 review ec47a651 rejected (its token denylist let
the hint silence camelCase, concatenated, unlisted and legacy request-like
statuses, and noise rows could carry results). Revision 3 adds the boundary,
per-root and punctuation-adjacent target hardening from RCO1 de8f1f2f and
RCO2 ec47a651 N1/N3-N6, and the literal falsy-value, `expected_responders`
key-casing and null noise-payload pins from RCO2 fc43943d N1.
Pure contract, reference implementation and golden vectors only. Nothing here
is wired into Watch-Bridge, Monitor-AgentBridge, the reboot status tool or any
runtime path; the PowerShell port and consumer/ops wiring are later reviewed
implementation slices of the one composed Bridge v2 package.

| Artifact | Path |
| --- | --- |
| Contract (this file) | `docs/architecture/BRIDGE_WAKE_CLASS_CONTRACT_V1.md` |
| Pure reference | `tools/bridge_wake_class.py` (`classify(event, target_agent)`) |
| Golden vectors | `tests/fixtures/wake_class/v1/vectors.json` |
| Conformance tests | `tests/tools/test_bridge_wake_class_vectors.py` |

## 1. Purpose and non-authority

`classify(event, target_agent)` decides whether one decoded bridge event
should wake `target_agent`'s inbox. **Wake eligibility is routing only.** It
grants no authority, validates no request binding, accepts no result, clears
no veto and is never an input to a merge, RCO or promotion gate. A consumer
that wakes still reads the canonical log and applies the request contract and
the gates.

Design rule: **suppression is allowlist-only.** A row is silenced by exactly
three narrow, exact shapes (section 5, steps 8, 9 and 17). Every other row
wakes; anything malformed, conflicting, unknown or merely unlisted is
`ambiguous` (ambiguity drains). A sender hint can never silence a control
signal, because no control word is on the benign list and control recognition
runs before the hint is consulted.

## 2. Input and output

* `event`: one JSON-decoded bridge row (any JSON value). Keys are
  case-sensitive; a case-variant spelling of an envelope key is never honoured.
* `target_agent`: a canonical lowercase agent name matching
  `[a-z0-9][a-z0-9._-]{0,127}`; anything else is a caller error (`ValueError`).

Output:

```json
{"contract": "wd.wake-class.v1", "class": "<class>", "wakes": true,
 "reason": "<reason>", "control_signal": false}
```

`wakes` is `false` exactly for the classes `notice`, `noise` and
`not_addressed`. `control_signal` is `true` when the type is a control type or
the status is a string that contains a control root (section 4). It is
computed for every object event, is a label that may over-wake, and never
decides suppression. The `noise` and `notice` exits never carry it by
construction. A consumer that routes by class must still send a
`request`/`bound_reply` row with `control_signal` true to its control handling.

## 3. Classes

| Class | Wakes | Meaning |
| --- | --- | --- |
| `request` | yes | Carries a well-formed top-level `request_id`. |
| `bound_reply` | yes | Carries a well-formed top-level `in_reply_to_request_id`. This is a **claimed** binding only; it is not validated here. |
| `control` | yes | A control type, or a notice type whose status contains a control root. |
| `notice` | no | `message`/`status`/`intent`, exact hint, status on the closed benign list. |
| `noise` | no | Liveness and received-ACK rows in their exact deployed shape. |
| `not_addressed` | no | Self-emitted, untargeted, or targeted at someone else. |
| `ambiguous` | yes | Everything else: malformed, conflicting, unknown, variant or unlisted. |

## 4. Vocabularies

* Notice types: `message`, `status`, `intent` (exact spelling).
* Control types: `decision`, `finding`, `blocked`, `rco_review`, `test`,
  `done`, `release`, `wake_request`.
* Liveness types: `heartbeat`, `liveness`; their payload may be absent, `null` or an
  object whose keys are only `head` and `notification`, with string values.
* ACK: type `message` with status `received`, `seen` or `acknowledged`
  (exact); payload absent, `null` or an object whose keys are only `request_ts_utc`,
  `request_agent`, `request_type`, `request_status` and `notification`, with
  string values. This is the deployed Read-AgentBridge received-ACK writer
  shape, measured on the canonical log on 2026-09-28.
* In any noise payload, `notification` must be exactly `informational`.
* **Benign notice statuses (closed allowlist, exact, case-sensitive):**
  `informational`, `info`, `notice`, `evidence`, `evidence_update`,
  `progress`, `progress_summary`, `in_progress`, `planning`.
* Why the allowlist is that small: the statuses were chosen narrowly from
  statuses measured under the hint; presence under the hint does not make a
  status benign, so result or promotion words (approved, verified, answered,
  completed_*, published, review_findings, ...) are deliberately absent.
  Adding a status is a contract change. A test checks that this list and the
  code list are equal in both directions.
* **Control roots (exact list):**
  `hold`, `held`, `veto`, `cancel`, `supersed`, `withdr`, `retract`, `revok`,
  `revoc`, `reject`, `refus`, `deny`, `denied`, `nack`, `fail`, `clos`,
  `stop`, `halt`, `abort`, `freez`, `frozen`, `quarantin`, `rollback`,
  `revert`, `changesrequested`, `paus`, `suspend`, `kill`, `incident`,
  `emergenc`, `escalat`, `error`, `timeout`, `expir`, `wedg`, `unsafe`,
  `invalid`, `conflict`, `regress`, `broke`, `critical`, `disapprov`, `nogo`,
  `embargo`, `lock`, `donot`, `notapprov`, `notpass`, `notmerg`, `notready`,
  `wait`.
* How roots match: the status is ASCII-lowercased and every non-alphanumeric
  character removed; it contains a control root when any root is a substring.
  This catches camelCase and concatenations (mergeHold, rcoveto, onhold) and
  over-wakes on purpose (threshold). No root contains another root (block was
  dropped as redundant with lock), so each root is independently pinned by a
  bound-reply vector; no benign status contains a root; a test checks that the
  code and doc root lists are equal in both directions. The root list is best
  effort and is **not** what makes suppression safe; the allowlist is.
* Ids: a well-formed id matches `[A-Za-z0-9][A-Za-z0-9._:-]{0,255}` with no
  trailing newline (at most 256 characters). A missing key, `null` or `""`
  means absent; any other value is malformed, including `0`, `false`, `" "`,
  `{}` and `[]`. Binding keys inside a payload follow the same absent rule:
  only `null` and `""` are absent, so `0`, `false`, `" "`, `{}` and `[]`
  count as present (never a truthiness test).
* Type and status must be non-empty ASCII strings of at most 256 characters
  (256 is accepted, 257 is `oversized_field`; both pinned).

## 5. Precedence

Steps run in order; the first match decides. Reason codes are exact.

1. Event is not a JSON object → `ambiguous` / `malformed_event`.
2. `agent` equals `target_agent` exactly and no case-variant `agent` key exists
   → `not_addressed` / `self_emission`.
3. Addressing. `to` is the routing source. Collect every non-empty value of a
   key spelled `to` or `expected_responders` in any case. None →
   `not_addressed` / `no_target`. The event is addressed only when the exact
   key `to` is the only `to`-like key, is a string, and one of its
   comma-separated, whitespace-trimmed entries equals `target_agent` exactly.
   Otherwise, if any collected value mentions the target loosely → `ambiguous`
   / `ambiguous_target`; else →
   `not_addressed` / `not_targeted`. A loose mention means: the
   ASCII-lowercased value (a non-string value as its JSON text, so an
   `expected_responders` key counts) contains the target name with no ASCII
   letter or digit directly before or after it. So `FABLE-5`, `fable-5;x`,
   `fable-5 x`, `fable-5.`, `fable-5-`, `_fable-5` and `(fable-5)` are
   ambiguous and wake, while another lane name that merely extends the target
   (`fable-50`, `xfable-5`, `claude-rco-10` for `claude-rco-1`) is not a
   mention. Ambiguous is not accepted routing: it only drains to the inbox,
   grants nothing, and exact canonical addressing is unchanged.
4. Any case-variant spelling of `agent`, `to`, `type`, `status`, `payload`,
   `request_id`, `in_reply_to_request_id` or `expected_responders` →
   `ambiguous` / `case_variant_key`.
5. Sender missing, non-string or blank → `ambiguous` / `missing_sender`;
   sender equal to the target only after trimming or case-folding →
   `ambiguous` / `sender_case_variant`.
6. Malformed `request_id` or `in_reply_to_request_id` → `ambiguous` /
   `malformed_request_id`.
7. Type or status missing, non-string or empty → `ambiguous` /
   `malformed_type_or_status`; non-ASCII → `non_ascii_field`; longer than 256
   characters → `oversized_field` (all `ambiguous`).
8. Liveness type: with a control root or any id → `ambiguous` /
   `conflicting_noise_signal`; payload outside the liveness shape →
   `ambiguous` / `noise_payload_not_recognized`; else `noise` / `liveness`.
9. ACK status: on any type other than `message`, or with a `request_id` →
   `ambiguous` / `conflicting_noise_signal`; payload outside the ACK shape
   (for example a `result`) → `ambiguous` / `noise_payload_not_recognized`;
   else `noise` / `ack`. A bound receipt (`in_reply_to_request_id` with the
   receipt shape) is noise; a bound row carrying a result is not.
10. Both ids well-formed → `ambiguous` / `conflicting_request_and_reply`.
11. `in_reply_to_request_id` → `bound_reply` / `claimed_reply`.
12. `request_id` → `request` / `request_id`.
13. Control type → `control` / `control_type`.
14. Any other non-notice type → `ambiguous` / `unknown_type`.
15. Control root in the status → `control` / `control_status`.
16. Payload missing or `null` → `ambiguous` / `unhinted_notice`; not an object
    → `ambiguous` / `malformed_payload`; a present (not `null`, not `""`)
    `request_id`, `in_reply_to_request_id`, `result` or `result_contract`
    (any key case) inside the payload → `ambiguous` / `payload_binding_field`; a case-variant
    `notification` key or any value other than the exact string
    `informational` → `ambiguous` / `notification_variant`; no `notification`
    → `ambiguous` / `unhinted_notice`.
17. Status not on the benign allowlist → `ambiguous` / `unlisted_status`;
    otherwise `notice` / `informational_hint`.

## 6. Differences from the deployed PowerShell consumers

The vectors record, for every case, what the deployed consumer filters return
today: Watch-Bridge `Test-IsTargeted` and the agent-inbox Monitor
`Test-SubstantiveMonitorEvent` (function bodies extracted verbatim from the
scripts, run in Windows PowerShell 5.1 and PowerShell 7, which agree on every
vector). A vector whose legacy outcome differs from the contract's wake
decision carries `legacy_difference` with both values and a note; the tests
check that the declared set is exactly the measured set. The contract is not
weakened to force parity.

Every declared difference is legacy-drops / contract-wakes, and a separate
test runs a corpus of more than 1,500 sampled rows through both deployed
consumers in both shells: no row that a consumer wakes on today is silenced by
the contract. (Candidate `bbc54f90` violated this for legacy request-like
statuses such as `review_requested` or `open` under the hint; revision 2 pins
them as vectors.) The difference classes are:

* Conflicting noise: a liveness row with a control status, an id or a
  non-liveness payload; an ACK status on a non-`message` type, with a new
  `request_id`, or with a result or other non-receipt payload.
* Control words the legacy exact-token list misses under the hint: camelCase
  and concatenations, plurals, `unblocked`, `frozen_*`, `quarantined_*`,
  `paused`, `incident`, `timeout`, `do_not_merge`, `not_approved`, the hyphen
  spelling `changes-requested`, and the deliberate over-wakes.
* Unlisted statuses under the hint (`review_findings`, `review_note`,
  `handoff_published`, unknown future statuses).
* Targets with the wrong separator or punctuation next to the name
  (`fable-5;x`, `fable-5 x`, `fable-5.`, `_fable-5`) are dropped by both
  consumers; a JSON array `to` is dropped by Monitor only; a target named
  only in `expected_responders` is dropped by both.
* A sender spelled like the target in another case is treated by legacy as
  self-emission. A missing or empty sender is dropped by Monitor but not by
  Watch.
* PowerShell property lookup is case-insensitive, so legacy honours a
  `Payload`/`Status`/`Notification` spelling, and `-ceq` on an array hint is
  truthy.
* Null, empty, integer, non-ASCII (including a Cyrillic `veto` homoglyph) and
  oversized statuses under the hint are suppressed by legacy.
* A binding or result field inside the payload of a hinted notice is
  suppressed by legacy.
* Non-object rows and rows with case-variant duplicate keys cannot be
  classified by legacy (`error` / `parse_error` in the vectors).

## 7. Limits

* `to` is the routing source. A typed control (`finding`, `decision`,
  `blocked`, ...) with no `to` and no `expected_responders` wakes nobody
  (`not_addressed` / `no_target`, `control_signal` true). There is no
  broadcast target (`*`, `all`, role names) in v1, matching legacy. Vetoes
  still bind through the gates, which scan the canonical log independently of
  wakes; a writer that wants a lane woken must address it.
* Free text (`message`) and free payload fields are not parsed. A HOLD written
  only in prose or as a payload flag inside a hinted notice with a benign
  status is not detected; vetoes and HOLDs must be typed events or carry a
  control status.
* `self_emission` trusts the `agent` field, as legacy does. A same-user
  forgery that sets `agent` to the recipient's own name **hides the wake from
  that recipient**. It does not hide the row from the gates or from other
  recipients, but the recipient's lane will not be woken. Sender integrity is
  a separate, mandatory integration (session-bound writer origin, plan F23);
  wake classification does not provide it.
* Duplicate-row suppression (Monitor's seen-set) is a later, separate stage
  and out of scope.
* `ops/windows/reboot/Get-WdSwarmParallelStatus.ps1` also consumes
  `Test-BridgeWakeEligible`; its projection is not measured by these vectors
  and stays explicitly unmeasured until it is (Lead's wiring slice must
  measure it). A test inventories every `Test-BridgeWakeEligible` call site
  under `.agent-bridge/bin` and `ops/` and fails on any consumer that is
  neither measured (Watch-Bridge, Monitor-AgentBridge) nor listed here as
  explicitly unmeasured.
* The legacy harness measures the extracted filter functions and only the
  agent-inbox Monitor configuration (`-TargetedOnly -IncludeWakeRequests`),
  not the main loops.
* Over-wake cost: RCO2 fc43943d replayed 3357 addressed (row, target) pairs
  from 2026-09-18..28 against revision 2 and the legacy Monitor filter:
  legacy woke 2992, the contract 3143, and 0 legacy-woken pairs were
  silenced. The +151 is one historical sample, not a forecast and not the
  fleet's paid cost. Frequent hinted statuses (`coordination`,
  `verification_passed`, `ci_green`, promotion notices, ...) stay off the
  benign allowlist; frequency alone never justifies widening it (vectors pin
  them as `unlisted_status`).
* Input domain: a JSON-decoded value. A non-JSON Python value (for example a
  set in `to`) raises `TypeError`; it cannot come from a decoded row.
* v1 freezes only on acceptance (both RCO reviews). After that, any change to
  vocabularies, precedence or outputs is `wd.wake-class.v2` with its own
  vectors; v1 vectors stay as a regression record.
