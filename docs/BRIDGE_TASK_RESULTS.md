# Structured task results and execution evidence

Delivery is separate from a valid task result. Use the full immutable request and the session's inherited `WD_BRIDGE_BIN`. Never replace it with the current installed pointer or manually reconstruct a peer identity.

## Before sending

`Write-BridgeTaskReply.ps1 -Agent <agent> -RequestEventJson <full-request> -ResultJson <object>` places the object under `payload.result`, copies the request's correlation fields and delegates to the existing canonical writer. It generates `execution_evidence` from the actual helper files, clock and process ancestry. No guessed UUID, conversation ID, model engine start, test duration or approval is generated.

Existing `wd.role-request.v1` requests enforce their exact `result_fields`. For other tasks, put this optional contract in the request payload:

```json
{
  "result_contract": {
    "schema": "wd.task-result-contract.v1",
    "required": ["answer", "quota"],
    "types": {"answer": "integer", "quota": "null"},
    "equals": {"answer": 42},
    "additional_properties": false
  }
}
```

This is a small task contract, not arbitrary JSON Schema. Fields are direct children of `payload.result`. Types are string, boolean, integer, finite number, object, array and null. Equality uses canonical JSON. Both the builder and public `Write-AgentEvent.ps1 -ReplyToEventJson` reject an invalid contracted substantive answer before append, spool or outbox publication. Legacy requests without a contract retain their transport behavior.

Independent review is an exception to requester-supplied content assertions: for either RCO identity or a `reviewer` role request, `equals` is never enforced and `content_valid` stays null. A requester cannot constrain the reviewer's verdict by demanding an expected answer. Structure still must match the agreed report format. The requester must assess the actual review, including disagreement, rather than treating its own expectations as approval criteria.

## Independent states

`Get-BridgeReplySnapshot.ps1` retains its existing transport `answered` state and adds per-answer validation:

- `correlation_valid`: exact request and session binding checked by the reader.
- `schema_valid`: result shape checked against the request; null when no contract exists.
- `content_valid`: only the explicit `equals` assertions were checked; null when none exist. This does not certify free-form explanations, evidence or general correctness.
- `reported`: null. A reply does not prove that a user summary was published or received. Existing `Record-BridgeReplyObservation.ps1` records an agent-reported publication reference separately, after publication.

Read the entire result and independently review substantive conclusions. A claimed passing test or a self-reported schema flag is not accepted as proof. Historical malformed replies remain visible with failed validation rather than disappearing from transport history.

## Evidence

`Get-BridgeExecutionEvidence.ps1` records observation start/end from the system clock, actual helper paths and SHA256 values, parser version, and native process/thread details when process ancestry exposes them. Observation time is not a test execution timestamp. Missing observations are null. Declared inherited helper pins must match the executing directory and externally anchored manifest; an observed launcher from another generation is a mismatch. It never follows `WD_REBOOT_STATE_CURRENT` to repin a live session. This detects accidental pin/identity misuse; it is not a security boundary against an authorized process fabricating all of its inputs.

## Wake policy

For a pure informational message use `payload.notification = "informational"`, without request or reply binding. It remains in canonical history but does not start a model turn. ACK and liveness traffic also do not wake. Unknown addressed traffic remains eligible. Requests and bound answers/corrections always remain eligible, including answers to already reported/closed tasks. Exact duplicate events are coalesced within a bounded watcher history; task IDs alone never define duplicates. Existing wake-file/debounce delivery remains in place.

## Reply cache

Each parser stamp gets its own hashed cache filename and lock. The old unpartitioned cache can remain unused; it is never trusted without verification. The index verifies the frozen prefix's file identity, generation, length and SHA256 without trying to parse later appends. A partial row present in the selected snapshot still fails; a row appended after its frozen boundary belongs to the next snapshot.
