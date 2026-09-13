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

In an interactive console the top status band and bounded discussion pane are
updated in place, only when their text/color or window geometry changes. There
is no clear-screen polling or appended per-poll status log. The display retains
at most 200 rendered lines in memory and shows the newest lines that fit the
window (at most 60 screen rows). Long screen lines have a `>` clipping marker;
this is a view, not an export of the canonical record. Unicode cell widths are
used to avoid wide characters wrapping over the next row.

The band shows `FOLLOW`/`PAUSED`, `READ-ONLY`, reader status/reason, unread bytes
from the **last bounded snapshot**, and session read-observation counts:
`rows`, `visible`, hidden heartbeat/liveness (`hb`), wake, message receipt/ACK,
filter, and deliberately skipped initial sample rows. These categories sum to
the rows read successfully. They are not whole-log totals, unique-event counts,
task completions or productive-work measurements; replay can count a row again.
`-InitialTail 0` samples at most one row to seed the cursor, so its skipped count
does not estimate the unseen historical row count. Missing/blocked reads and
pause show unknown lag rather than implying an up-to-date reader.

Controls affect only this window:

- `P` or Space pauses/resumes. Pause does not read or advance the cursor; resume
  continues bounded reads from the retained cursor, with normal gap detection.
- `A` cycles ALL/Lead/Tools/RCO1/RCO2/Fable; `T` cycles ALL and common event types.
  A changed filter clears the displayed pane and applies to subsequent reads;
  it does not rescan history or reset session counts. Startup `-AgentFilter` and
  `-TypeFilter` accept exact, case-sensitive values, not wildcard expressions.
- `Q` exits the viewer only. None of these keys sends a command to an agent.

Rows include `[LEAD]`, `[TOOLS]`, `[RCO1]`, `[RCO2]`, `[FABLE]` or `[OTHER]`, plus
explicit `[ERROR]`/`[WARN]` when indicated by severity **or** status. Sender,
recipient and task labels remain textual in plain output; the narrow dashboard
uses short UTC time and role/type/status badges to leave room for the message.
ACK suppression follows message
receipt statuses, not hypothetical extra event types.

`-PlainText` forces streaming output without keyboard controls; redirected
input/output selects it automatically. Plain mode emits discussion and changed
diagnostics, with one final counters summary on a normal bounded run or exit,
not a new summary for every heartbeat-only poll. An unavailable interactive
console visibly falls back to the plain stream. This version has no web server,
second event store, progress estimate, or provider-token counter.

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
