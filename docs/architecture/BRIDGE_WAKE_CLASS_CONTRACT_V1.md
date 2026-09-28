# Bridge wake-class contract v1 (`wd.wake-class.v1`)

Status: W0 building block (Bridge v2 single-release plan). Pure contract,
reference implementation and golden vectors only. Nothing here is wired into
Watch-Bridge, Monitor-AgentBridge, the reboot status tool or any runtime path;
F30 consumer wiring and the PowerShell `Get-BridgeWakeClass` port are separate,
later, reviewed steps.

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

Design rule: every exit that does **not** wake is exact and demonstrably
non-actionable. Anything malformed, conflicting, unknown or spelled in a
variant way is `ambiguous` and **wakes** (ambiguity drains). A control signal
can never be suppressed by a sender hint.

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
`not_addressed`. `control_signal` is `true` when the event's type is a control
type or its status is a string carrying a control token (section 4). It is
computed for every object event; the `noise` and `notice` exits never carry
one by construction (a malformed, non-string status yields `ambiguous`, not a
control signal). It lets a consumer that routes by class keep a veto that
arrived inside a reply or request.

## 3. Classes

| Class | Wakes | Meaning |
| --- | --- | --- |
| `request` | yes | Carries a well-formed top-level `request_id`. |
| `bound_reply` | yes | Carries a well-formed top-level `in_reply_to_request_id`. This is a **claimed** binding only; it is not validated here. |
| `control` | yes | A control type, or a notice type whose status has a control token. |
| `notice` | no | `message`/`status`/`intent` with the exact hint `payload.notification == "informational"` and no control token. |
| `noise` | no | Exact liveness rows and exact ACK rows. |
| `not_addressed` | no | Self-emitted, untargeted, or targeted at someone else. |
| `ambiguous` | yes | Everything else: malformed, conflicting, unknown or variant. |

## 4. Vocabularies

* Notice types: `message`, `status`, `intent` (exact spelling).
* Control types: `decision`, `finding`, `blocked`, `rco_review`, `test`,
  `done`, `release`, `wake_request`.
* Liveness types: `heartbeat`, `liveness`. ACK statuses: `received`, `seen`,
  `acknowledged` (exact spelling).
* Control tokens: the status is ASCII-lowercased and split on every
  non-alphanumeric character. A token is a control token when the token, or
  the token without a leading `un`, **starts with** one of these roots:
  `hold`, `held`, `veto`, `block`, `cancel`, `supersed`, `withdr`, `retract`,
  `revok`, `revoc`, `reject`, `refus`, `deny`, `denied`, `nack`, `fail`,
  `clos`, `stop`, `halt`, `abort`, `freez`, `frozen`, `quarantin`,
  `rollback`, `revert`. The adjacent token pair `changes` `requested` (any
  separator) is also a control signal. Prefix matching over-wakes on purpose
  (for example `failover`): a spurious wake costs one turn, a missed veto
  costs safety. Every token of the legacy `Test-BridgeInformationalNoticeSuppressible`
  list is a control token here (pinned by a test).
* Ids: a well-formed id matches `[A-Za-z0-9][A-Za-z0-9._:-]{0,255}`. A missing
  key, `null` or `""` means absent; any other value is malformed.
* Type and status must be non-empty ASCII strings of at most 256 characters.

## 5. Precedence

Steps run in order; the first match decides. Reason codes are exact.

1. Event is not a JSON object → `ambiguous` / `malformed_event`.
2. `agent` equals `target_agent` exactly and no case-variant `agent` key exists
   → `not_addressed` / `self_emission`.
3. Addressing. Collect every non-empty value of a key spelled `to` in any case.
   None → `not_addressed` / `no_target`. The event is addressed only when the
   exact key `to` is the only such key, is a string, and one of its
   comma-separated, whitespace-trimmed entries equals `target_agent` exactly.
   Otherwise, if any such value mentions the target loosely (case-insensitive,
   any separator, or inside a non-string value) → `ambiguous` /
   `ambiguous_target`; else → `not_addressed` / `not_targeted`. There is no
   broadcast target in v1.
4. Any case-variant spelling of `agent`, `to`, `type`, `status`, `payload`,
   `request_id` or `in_reply_to_request_id` → `ambiguous` / `case_variant_key`.
5. Sender missing, non-string or blank → `ambiguous` / `missing_sender`;
   sender equal to the target only after trimming or case-folding →
   `ambiguous` / `sender_case_variant`.
6. Malformed `request_id` or `in_reply_to_request_id` → `ambiguous` /
   `malformed_request_id`.
7. Type or status missing, non-string or empty → `ambiguous` /
   `malformed_type_or_status`; non-ASCII → `non_ascii_field`; longer than 256
   characters → `oversized_field` (all `ambiguous`).
8. Liveness type: with a control token or any id → `ambiguous` /
   `conflicting_noise_signal`; else `noise` / `liveness`.
9. ACK status: on a non-notice type or with a `request_id` → `ambiguous` /
   `conflicting_noise_signal`; else `noise` / `ack` (a bound ACK is noise).
10. Both ids well-formed → `ambiguous` / `conflicting_request_and_reply`.
11. `in_reply_to_request_id` → `bound_reply` / `claimed_reply`.
12. `request_id` → `request` / `request_id`.
13. Control type → `control` / `control_type`.
14. Any other non-notice type → `ambiguous` / `unknown_type`.
15. Control token in the status → `control` / `control_status`.
16. Payload missing or `null` → `ambiguous` / `unhinted_notice`; not an object
    → `ambiguous` / `malformed_payload`; a non-empty `request_id` or
    `in_reply_to_request_id` (any key case) inside the payload → `ambiguous` /
    `payload_binding_field`; a case-variant `notification` key or any value
    other than the exact string `informational` → `ambiguous` /
    `notification_variant`; no `notification` → `ambiguous` /
    `unhinted_notice`; otherwise `notice` / `informational_hint`.

## 6. Differences from the deployed PowerShell consumers

The vectors record, for every case, what the deployed consumer filters return
today: Watch-Bridge `Test-IsTargeted` and the agent-inbox Monitor
`Test-SubstantiveMonitorEvent` (function bodies extracted verbatim from the
scripts, run in Windows PowerShell 5.1 and PowerShell 7, which agree on every
vector). A vector whose legacy outcome differs from the contract's wake
decision carries `legacy_difference` with both values and a note; the tests
check that the declared set is exactly the measured one. The contract is not
weakened to force parity. At v1 there are 43 differences, all in the
direction legacy-drops / contract-wakes:

* Conflicting noise: a liveness row with a control status or an id, an ACK
  status on a `decision`/`finding`/`wake_request` row, and an ACK carrying a
  new `request_id` are all dropped by legacy.
* Control words missing from the legacy exact-token list under the
  informational hint: plurals (`merge_holds`), `unblocked`, `frozen_*`,
  `quarantined_*`, `merge_refused`, the hyphen spelling `changes-requested`,
  and the deliberate over-wake `failover_*`.
* Targets with the wrong separator (`fable-5;x`, `fable-5 x`) are dropped by
  both consumers; a JSON array `to` is dropped by Monitor only.
* Sender spelled like the target in another case is treated by legacy as
  self-emission. A missing or empty sender is dropped by Monitor but not by
  Watch.
* PowerShell property lookup is case-insensitive, so legacy honours a
  `Payload`/`Status`/`Notification` spelling, and `-ceq` on an array hint is
  truthy.
* Null, empty, integer, non-ASCII (including a Cyrillic `veto` homoglyph) and
  oversized statuses under the hint are suppressed by legacy.
* A binding field only inside the payload of a hinted notice is suppressed by
  legacy.
* Non-object rows and rows with case-variant duplicate keys cannot be
  classified by legacy (`error` / `parse_error` in the vectors).

Legacy wakes in some cases where the contract also wakes but for a different
reason (for example `to: "FABLE-5"` matches case-insensitively in legacy and is
`ambiguous_target` here); those are not differences.

## 7. Limits

* Free text (`message`) is not a control channel and is never parsed. A HOLD
  written only in prose inside a hinted notice is not detected; vetoes and
  HOLDs must be typed events (`finding`, `decision`, ...) or carry a control
  status. The veto gates read the log independently of wakes.
* `self_emission` trusts the `agent` field, as legacy does. Same-user
  forgery of `agent` can hide a row from its forged author's inbox only; it
  cannot hide it from the gates or from its real recipients.
* Duplicate-row suppression (Monitor's seen-set) is a later, separate stage
  and out of scope.
* `ops/windows/reboot/Get-WdSwarmParallelStatus.ps1` also consumes
  `Test-BridgeWakeEligible`; its projection is not measured by these vectors.
* v1 is frozen. Any change to vocabularies, precedence or outputs is
  `wd.wake-class.v2` with its own vectors; v1 vectors stay as a regression
  record.
