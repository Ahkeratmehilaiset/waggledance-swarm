# Bridge observability v1

These views report evidence, not authority. They do not grant permission to
start agents, acknowledge tasks, merge, deploy, or change the deliberate HOLD.
Committing these changes does not update an installed, hash-bound reboot bundle.

## Conversation window

After a verified bundle deployment, a successful fleet `-Apply` (including the
generated wrapper's `-Auto` path) opens **WD Bridge Conversation** in a separate
visible Windows PowerShell 5.1 window. `-DryRun` only describes that action.
`-NoBridgeConversation` suppresses the window and is forwarded through elevation.
The elevated caller waits for restoration, not the lifetime of the viewer.

The viewer can also run independently from a source checkout, without starting
any fleet agents:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\windows\reboot\Show-WdBridgeConversation.ps1 -RuntimeRoot C:\Python\project2-master\.agent-bridge
```

Sender colors are cyan (lead), green (Tools), yellow (RCO1), magenta (RCO2),
blue (Fable), and gray (other agents). Errors override the color with red;
warnings use yellow. Text labels identify sender, event type, status, and
severity, so meaning does not depend on color alone.

The viewer shows at most 40 physical rows initially and then follows bounded
byte deltas. Heartbeat/liveness, wake, and message-ACK traffic is hidden. This
is a discussion view, not a complete audit transcript or proof of activity.
Defaults: one-second polling, 200 rows/4 MiB per delta, 4 MiB per initial tail,
and a 1,200-character message display cap with an explicit truncation marker.
`-InitialTail 0` skips existing rows; `-MaxIterations` bounds a diagnostic run.
Rotation/truncation announces possible history loss before bounded replay;
malformed input reports the blocked read and retains the cursor.

Only the existing `BridgeIncrementalReader.ps1` / `BridgeLogReader.ps1` readers
are used. Cursors remain in memory: no ACK, queue drain, stale-claim sweep,
checkpoint, or log write occurs. A session-local mutex prevents duplicate
viewers for the same normalized runtime path. Closing the window stops only
the viewer. Terminal-control characters are sanitized before display.

## Evidence-aware fleet status

```powershell
.\ops\windows\reboot\Get-WdSwarmParallelStatus.ps1 -Json
```

The existing status report keeps its legacy fields for compatibility, including
`runnable` / `runnable_lanes`. Those fields mean only that a checkpoint records
a next action; they are not evidence of a running or progressing agent.

New fields separate checkpoint freshness, actual checkout HEAD, the installed
pointer, scheduled Supervisor state, and runtime observations. Tools readiness
is compared with an observed PID, start time, and generation. A matching record
is not full process attestation. Other lanes without readiness evidence remain
unknown. A disabled Supervisor remains separately visible and is not enabled.

`runnable_evidence = observed` requires a current matching checkpoint with a
recognized active status, no recorded blocker, an enabled Supervisor, and a
matching ready process. Missing or ambiguous evidence remains `unknown`;
explicit contrary evidence is `not_observed`. Heartbeats, wake files, and ready
timestamps do not establish substantive progress or wait age.

## Compact-view measurement

```powershell
python -B tools/measure_bridge_compact_view.py --events C:\Python\project2-master\.agent-bridge\shared\events.jsonl --tail 5000
```

The report compares UTF-8 bytes of the selected canonical JSON array with the
compact CLI envelope, including its cursor and reader metadata. It records a
selected-corpus hash, event-reference/chronology coverage, duplicate and
heartbeat exclusions, traffic categories, and observed read/render duration.
These are bounded-snapshot measurements, not whole-history or on-disk savings.
Provider token and monetary cost fields remain unknown; hex/ASCII encoding is
not used as a substitute for measurement. Full payload preservation is not
claimed; use the existing event-detail reader when required.

ACK timing is reported only for unambiguous matching recorded requests.
Queued/accepted/suppressed transport is not canonical delivery, ACK is not
completion, and a non-heartbeat traffic count is not productive work.

## Recovery and consultation telemetry

The hourly Grok helper records elapsed consultation time and a finishing time
in its existing budget state. `-Status` remains byte-inert. Interrupted runs
retain their prior budget reservation; elapsed time does not grant a refund.

Isolated tests reload durable checkpoints in a fresh PowerShell process with
an orphan partial temporary file present, reject invalid updates without
changing the canonical file, and exercise the current normal `-Tail 0` reader.
These tests and fleet `-DryRun` do not substitute for an actual post-reboot
handshake. The documented fleet host remains Windows PowerShell 5.1.

## Validation

Focused files are `test_wd_swarm_parallel_status.py`,
`test_measure_bridge_compact_view.py`, `test_wd_grok_helper.py`,
`test_wd_bridge_conversation.py`, `test_wd_recovery_readiness.py`, and
`test_wd_reboot_bundle.py` under `tests/tools/`.

Run the affected-test selector first. Its fail-safe result for the PowerShell
paths requires the full local suite once the final candidate is assembled;
CI remains the authoritative exact-head full-suite gate before any merge.
Source review is not a formal RCO vote or runtime activation approval.
