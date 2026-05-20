# Idle Loop Runbook

**Status:** operator-authorized 2026-05-20
**Companion docs:** `IDLE_PROTOCOL_V1.md`, `IDLE_AUTONOMY_CHARTER.md`,
`IDLE_CONSENSUS_ARTIFACT_V1.md`, `MAGMA_SUBSTRATE_AUDIT_2026_05_17.md`.
**Companion code:** `tools/idle_loop_once.py`.

## Purpose

This runbook documents how the operator turns the manual, opt-in
idle-protocol v1 into an unattended continuous loop without weakening
any charter gate. The continuous-loop ambition (operator quote
2026-05-20: *"molemmilla täytyy olla tehtäviä jatkuvassa silmukassa
ilman minun aktivointia"*) is achieved by combining:

1. `tools/idle_loop_once.py` (Slice 1 of the agent-continuous-loop work)
   — a read-only one-tick orchestrator that observes bridge state and
   reports a single decision token plus a `next_action`.
2. An OS-level scheduler (Windows Task Scheduler or cron) that fires
   `idle_loop_once.py --json` on interval. The scheduler is installed
   **once** by the operator; afterwards the loop runs unattended.
3. An LLM-in-the-loop step that consumes the decision token and
   composes the substantive bridge event when one is required.

Honest substrate limit: Claude Code and Codex CLI are interactive
sessions, not background daemons. The scheduler entry replaces the
operator's *"continue"* nudge with an OS timer, but model-generated
idle-protocol payloads still require an active agent session at the
moment of the tick. `idle_loop_once.py` is intentionally agnostic of
which agent (or human operator) acts on its recommendation.

## What gets installed

* One Windows Task Scheduler entry (or cron job on Linux) that runs
  `idle_loop_once.py --json` every 30 minutes by default.
* The schedule entry **only invokes the read-only observer**; it does
  not start a model session, emit bridge events, or open PRs.
* A small wrapper script (operator-owned) that pipes the JSON decision
  into the next concrete action per the
  [Decision-action map](#decision-action-map) below.

There is no daemon, no long-running Python process, and no model API
call inside the scheduled task itself.

## Decision-action map

`idle_loop_once.py --json` emits one of these decision tokens. The
wrapper or follow-up agent uses the map below to drive the next step.

| `decision` | `next_action` | Operator/agent follow-up |
|---|---|---|
| `not_idle` | `wait_for_quiet` | Do nothing. Real work is in progress; the bridge correctly refuses a new idle round. |
| `no_session` | `emit_round_1` | Invoke `tools/run_idle_protocol_once.py --emit --json` from a Claude or Codex session that has agent identity (`AGENT_BRIDGE_RUNTIME_ROOT` set). Emits one round-1 `idle_proposal` event. |
| `mid_protocol` | `generate_next_round_payload` | Read `session_summary.next_required_event` from the report and pass it to the appropriate agent (Codex for round 2 + 4 counter-proposals when Claude opened round 1; Claude for round 3 adversarial review; either agent for round 5 consensus). Each agent composes the substantive payload and emits via `tools/idle_protocol_activate.py --apply`. **Payload generation is intentionally model-in-the-loop**; no tool generates payloads automatically. |
| `convergence_reached` | `route_to_implementer_chain` | The protocol reached `soft_convergence` (round 5+, ≥2 supporters of same target) or `hard_convergence` (round 10). Auto-conversion from consensus to implementation work is **deferred** in `IDLE_PROTOCOL_V1.md`. An implementer agent (Claude or Codex) must turn the converged proposal into a candidate diff first, then the chain runs: `idle_consensus_artifact` → `idle_consensus_draft_pr` → `pr_status_snapshot` → `idle_consensus_auto_merge --apply`. The auto-merge step enforces the 7 charter conditions before any external effect. |
| `operator_review_required` | `operator_handles` | Idle protocol hit a terminal state that requires operator review: `charter_violation`, `invalid_event`, or `operator_escalation` (low-quality response). The wrapper should page the operator (`docs/handoffs/idle_escalation_*.md` or any inbox the operator monitors). |
| `unknown` | `operator_handles` | `idle_check` could not prove the bridge state (e.g., missing or corrupt `shared/events.jsonl`). The scheduler should **leave the bridge alone** and surface this to the operator. Do not start a new round under uncertainty. |

## Windows Task Scheduler XML

The minimal task definition below runs `idle_loop_once.py` every 30
minutes against the project's runtime bridge root and writes the
machine-readable report into a rotating log file. Save as
`%USERPROFILE%\IdleLoopOnce.xml` and import with
`schtasks /Create /XML "%USERPROFILE%\IdleLoopOnce.xml" /TN "WaggleDanceIdleLoopOnce"`.

```xml
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>WaggleDance idle-protocol one-tick observer</Description>
    <Author>operator</Author>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition>
        <Interval>PT30M</Interval>
      </Repetition>
      <StartBoundary>2026-05-20T08:00:00</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-21-...</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT2M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>C:\Python\project2-master\.venv\Scripts\python.exe</Command>
      <Arguments>C:\Python\project2-master\tools\idle_loop_once.py --events C:\Python\project2-master\.agent-bridge\shared\events.jsonl --claims-dir C:\Python\project2-master\.agent-bridge\work_queue\claims --idle-minutes 60 --json</Arguments>
      <WorkingDirectory>C:\Python\project2-master</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
```

Replace `S-1-5-21-...` with the operator's SID (run `whoami /user` to
obtain). `RunLevel` is `LeastPrivilege` because the observer reads
local files only; no elevation is required.

Operator-owned wrapper to dispatch on the decision token (PowerShell
sketch):

```powershell
$reportJson = & "C:\Python\project2-master\.venv\Scripts\python.exe" `
  "C:\Python\project2-master\tools\idle_loop_once.py" `
  --events "C:\Python\project2-master\.agent-bridge\shared\events.jsonl" `
  --claims-dir "C:\Python\project2-master\.agent-bridge\work_queue\claims" `
  --idle-minutes 60 --json
$report = $reportJson | ConvertFrom-Json
$logRoot = "$env:USERPROFILE\waggledance_idle_loop_log"
New-Item -ItemType Directory -Force $logRoot | Out-Null
$reportJson | Out-File -Encoding utf8 (Join-Path $logRoot "$(Get-Date -Format 'yyyyMMddTHHmmss')-$($report.decision).json")
# Wrapper-specific dispatch (operator wires the right agent here):
switch ($report.decision) {
    'no_session'                  { <# invoke an LLM session to run run_idle_protocol_once --emit #> }
    'mid_protocol'                { <# page the agent whose turn the next_required_event names #> }
    'convergence_reached'         { <# page implementer agent to write candidate diff #> }
    'operator_review_required'    { <# write docs/handoffs/idle_escalation_$(Get-Date -Format 'yyyyMMddTHHmmss').md #> }
    'unknown'                     { <# write docs/handoffs/idle_unknown_$(Get-Date -Format 'yyyyMMddTHHmmss').md #> }
    'not_idle'                    { } # no-op; loop will catch it on the next tick
}
```

The wrapper itself is intentionally not committed to this repo: it
contains operator-machine-specific paths and operator-chosen agent
dispatch policy. The runbook example above is the minimum that fits
on one screen for transparency.

## Cron equivalent (Linux / WSL)

For operators running the bridge from WSL or a Linux host, the
equivalent crontab entry:

```cron
*/30 * * * * cd /mnt/c/Python/project2-master && /mnt/c/Python/project2-master/.venv/Scripts/python.exe tools/idle_loop_once.py --events .agent-bridge/shared/events.jsonl --claims-dir .agent-bridge/work_queue/claims --idle-minutes 60 --json >> "$HOME/waggledance_idle_loop_log/$(date +\%Y\%m\%dT\%H\%M\%S).json" 2>&1
```

The dispatch wrapper is analogous; a shell script that runs `jq` over
the JSON and branches on `.decision`.

## Escape hatch

The runbook is **revocable by the operator at any time**. Three
removal layers in increasing strength:

1. **Disable the schedule entry.** `schtasks /Change /TN
   "WaggleDanceIdleLoopOnce" /DISABLE` (Windows) or
   `crontab -e` and comment out the line (Linux). The loop stops
   immediately. No state is lost; re-enabling restores the cadence.
2. **Remove the schedule entry.** `schtasks /Delete /TN
   "WaggleDanceIdleLoopOnce" /F` (Windows) or remove the line from
   `crontab -e`. This is the same effect as disable plus removal of
   the registration.
3. **Charter-level revocation.** Per `IDLE_AUTONOMY_CHARTER.md`
   §Revocation, the operator can revert the charter, add a
   `CHARTER_DISABLED` constant in `tools/idle_consensus_to_pr.py`
   (operator-merge only by code-pattern denylist), or send a direct
   bridge-message instruction to both agents. Any of these three
   makes the autonomous-merge step at the end of the chain refuse to
   run; the scheduled `idle_loop_once.py` itself keeps returning
   read-only reports but the merge that would have followed never
   fires.

## Charter alignment

`idle_loop_once.py` and this runbook do not weaken any existing
charter gate. They wire together primitives the charter already
authorizes:

* The seven parallel conditions for autonomous merge in
  `IDLE_AUTONOMY_CHARTER.md` are enforced unchanged by
  `tools/idle_consensus_auto_merge.py` (the chain's terminal step).
* The code-pattern denylist (no edits to `auto_execute=False` /
  `operator_gate_required=True` constants) is untouched.
* The file-path denylist is untouched. This runbook is a **new** file
  under `docs/architecture/**`; it is not listed as denylisted.
* The self-modification ban on `IDLE_PROTOCOL_V1.md`,
  `IDLE_AUTONOMY_CHARTER.md`, and `tools/idle_consensus_to_pr.py`
  is respected.
* `IDLE_PROTOCOL_V1.md` §Deferred items remain deferred: model-
  generated payloads are still produced by an active LLM agent, not
  by this tooling. The scheduler does not synthesize protocol events.

## Smoke verification

Operators can verify the runbook end-to-end without enabling the
schedule entry:

```powershell
# 1. One-tick observer against the live bridge:
C:\Python\project2-master\.venv\Scripts\python.exe `
  C:\Python\project2-master\tools\idle_loop_once.py `
  --events C:\Python\project2-master\.agent-bridge\shared\events.jsonl `
  --claims-dir C:\Python\project2-master\.agent-bridge\work_queue\claims `
  --idle-minutes 60 --json

# 2. Confirm decision token is in the documented set:
#    not_idle / no_session / mid_protocol / convergence_reached /
#    operator_review_required / unknown

# 3. Unit-test the decision branches:
C:\Python\project2-master\.venv\Scripts\python.exe -m pytest `
  tests/tools/test_idle_loop_once.py -q
```

If the unit suite passes (13/13) and the live invocation returns a
decision in the documented set, the substrate is healthy. Install
the schedule entry only after verifying both.

## What this runbook does NOT do

* Does **not** make either agent run autonomously. Both Claude Code
  and Codex CLI remain interactive sessions; the operator (or a
  separate `tools/agent_next_task.py` Slice 3, future) is what brings
  an agent online to act on `mid_protocol` or `convergence_reached`.
* Does **not** generate idle-protocol payloads. Payload generation is
  LLM-in-the-loop by design (see
  [`IDLE_PROTOCOL_V1.md`](IDLE_PROTOCOL_V1.md) §Deferred).
* Does **not** auto-convert consensus into implementation work.
  Auto-conversion remains deferred.
* Does **not** modify any operator-gate constant or charter
  threshold. The 7-condition gate in
  `tools/idle_consensus_auto_merge.py` is the only authority for the
  final merge step.

## Versioning

This is v0 of the runbook. Future revisions should:

* Update the schedule cadence (`Interval` in the XML) only after
  measuring tick latency and bridge event-rate per UTC day; default
  `PT30M` matches the 60-minute idle_check window with one
  margin tick.
* Add a section on `tools/agent_next_task.py` (Slice 3) once that
  primitive lands.
* Cross-link to any production two-agent activation loop
  implementation once `IDLE_PROTOCOL_V1.md` lifts that deferral.
