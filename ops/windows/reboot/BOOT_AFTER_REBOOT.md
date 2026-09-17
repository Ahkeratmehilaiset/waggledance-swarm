# WaggleDance: reboot recovery

Tools cold-start validation rejects an unresolved previous owner or local
`.pending` journal before CLI updates and supervisor startup. Preserve that
evidence and reconcile the interrupted attempt before restarting; a readiness
timeout does not establish that the previous work completed.

When the operator explicitly identifies unrelated Codex/Claude sessions, an
individual restore can pass `-ExternalSessionsPath <absolute-json-path>` and
`-ExternalSessionsHash <SHA256>` to `start-wd-all.ps1` (including `-Auto`). The
`wd.external-agent-sessions.v1` snapshot contains `expires_at_utc` (within 24
hours) and `processes`: exact `pid`, `name`, `process_start_utc`,
`executable_path`, and `command_line` values from the reviewed process snapshot.
These sessions remain external and are never adopted or stopped. A changed
process lifetime, changed snapshot, expired approval, or same-lane launcher
still blocks startup. Without the explicit parameters, admission is unchanged.

The single-command reboot entry point is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Python\start-wd-all.ps1 -Auto
```

`-Auto` runs the byte-inert DryRun first and proceeds to Apply only when that
preflight returns successfully. It is the recommended operator command after
Windows sign-in. It may be launched from an ordinary PowerShell. The verified
wrapper requests one Windows UAC elevation before preflight when Task Scheduler
changes require Administrator rights; accept that prompt to continue. An
already elevated PowerShell does not prompt again.

Cold-start preflight resolves each native lane's saved conversation before CLI
updates or scheduler changes. Lead uses its reconciled recorded Codex thread ID
in the normal Codex terminal (`gpt-6-astra`, `xhigh`). RCO1, RCO2 and Fable resume
the newest **named lane conversation** in their own canonical worktree with
`claude --resume <exact-UUID>`; they never use account-wide `--continue` or fork
the conversation. Selection uses the first main-thread timestamp, not file
modification time. Conflicting, incomplete, or ambiguous named history stops
preflight instead of silently opening an empty conversation. A genuinely empty
history starts the initial visual bootstrap once.

The continuation turn checks compact state and live bridge claims, then resumes
the latest unfinished authorized work. Completed effects are reconciled before
retrying; cancelled work and explicit operator pauses/HOLDs stay stopped. Native
Lead has no managed idle-wake consumer attached to its terminal. Tools has an
automatic bridge relay: the wrapper polls its targeted wake sentinel once per
second and uses `codex queue --thread <saved UUID> --message <notification>`.
Codex receives the notification while idle and serializes it behind an active
turn. Window focus and minimization do not affect delivery. Operator messages
and `/model` continue to use the normal Codex UI.

Wake bursts are coalesced over five seconds. The relay moves the sentinel to an
owned snapshot and records submission before invoking Codex. A confirmed queue
receipt consumes that snapshot; newer wake writes remain for the next delivery.
An uncertain queue result stops automatic delivery with evidence preserved,
rather than replaying an ambiguous attempt. Queue acceptance is not task
completion. The exclusive relay lock prevents concurrent delivery helpers.

Tools uses `conversation_surface=native_terminal`: the Limited supervisor opens
one Windows Terminal window named `codex-tools-1`, running normal Codex with its
recorded conversation UUID, `gpt-5.6-terra/high` and workspace-write permissions.
The launcher holds the existing Tools ownership lock for the terminal lifetime.
The former custom UI and its Automation toggle are inactive on this path. The
saved conversation and interrupted-work evidence are preserved; unresolved
attempts still block a replacement. A live Tools process from another generation
requires a controlled handoff, rather than automatic process termination.

Tools readiness v3 means `terminal_ready` with scope `native_cli_only`: the
wrapper and native process identities match. It does not claim a completed model
turn or ongoing task progress. Check those in the visible terminal and bridge
evidence. Closing Codex ends this Tools session. `/model` remains available.

Already-live lane wrappers are identified using their original deployment hash,
including wrappers whose process command line omits `-ManifestPath`. CLI binary
changes after launch or other identity mismatches still require a controlled
handoff; a launch plan must never guess that a conflicting process is disposable.

Each elevated `-Auto` run keeps a transcript under
`C:\Python\wd-reboot-runtime\elevated-auto`. If the elevated process fails, the
parent PowerShell prints the transcript tail and its exact path.

Its non-mutating verification mode is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Python\start-wd-all.ps1 -DryRun
```

For a manual two-step recovery, run `-DryRun` and then `-Apply`. With no mode
switch the launcher defaults to byte-inert DryRun. After a successful restore,
leave each agent's conversation window open. Lead and Tools use standard Codex
terminals, and RCO1, RCO2 and Fable
retain their native interactive windows.
Their contexts are independent, not multiple views of Lead. Tools and exactly
five real-time bridge watchers are reconciled through `WD-Supervisor`; the Tools
window never creates an additional consumer alongside its existing parent.

Legacy Tools local-window readiness v2 reports verified transport availability separately
from native checkpoint progress. An open window is not evidence of useful work.
In legacy headless mode, readiness v1 follows the first tick, which can take
several minutes. During the bounded readiness wait, `-Auto` prints progress every
30 seconds. A present but unattested record is a launcher/process-identity problem.
The colored read-only bridge monitor opens after successful fleet restoration;
use `-NoBridgeConversation` only when deliberately opting out of that extra view.
This separate colored monitor is the default operational bridge view. The bridge
tab inside either conversation GUI is currently an unconnected placeholder, not
an integrated monitor. Closing the Tools window stops only its consumer; because
the supervisor is returned to Disabled/HOLD after restore, it stays stopped until
a later deliberate supervisor run.

The elevated restore never launches the five bridge watchers or Tools
directly. It demand-starts the exact `RunLevel=Limited` WD-Supervisor task once,
waits for that scheduled path to finish successfully, and returns the task to
Disabled/HOLD while the interactive lanes are restored. This keeps every
supervisor-owned process visible to later Limited supervisor runs and prevents
an elevated/Limited duplicate-generation race. The task is enabled permanently
only after the complete fleet and bridge baseline have passed verification.

Only for legacy interactive Lead configurations without `native_resume_policy`, after the
`codex-lead-1` lane has completed its bridge-bootstrap
handshake, the restore also reconciles exactly one separate Codex prompt-watcher
window. It targets only the terminal title `codex-lead-1` and runs the bundled,
hash-verified `Watch-CodexPrompts.ps1` with `-AllowAll -NoAllNighter`. This is
intentionally dangerous: `-AllowAll` bypasses both that script's command
allowlist and denylist and can approve any Codex command prompt it recognizes
after the desktop-idle guard permits input. Keep the prompt-watcher window open
only while this unattended behavior is intended. Claude lanes already use
`--dangerously-skip-permissions`, and Tools uses approval policy
`never`; neither receives a UI prompt watcher.

DryRun verifies the prompt-watcher script and reports whether it would keep or
launch the single Lead watcher. A non-canonical Lead watcher or more than one
watcher targeting `codex-lead-1` is an ambiguous conflict and stops recovery
before CLI updates or process launches. The prompt watcher is separate from the
five supervisor-managed real-time bridge watchers. The native Lead default
does not launch this UI approval watcher; a pre-existing watcher blocks the
Lead startup until it is deliberately closed through a controlled handoff.
Failure to materialize a
new watcher window after all lane handshakes is non-fatal: the launcher warns,
leaves unattended Lead prompt approval disabled, and still completes the
verified fleet restore. A later `-Auto` run reconciles the watcher again.

The DryRun includes the supervisor's byte-inert watcher plan. A single stale
watcher is replaceable only when its command tuple, identity, runtime root,
bundle-generation path, deployment manifest, and script hash all verify. Any
unverified or duplicate watcher, persistent replacement marker, or busy
reconciliation mutex blocks watcher and Tools reconciliation and fails the
launcher before CLI or Grok mutation. The supervisor still enforces the
merge-driver HOLD before returning that conflict.

The command performs a whole-fleet preflight before opening a window. An exact
`WD-Supervisor` task held Disabled by a controlled deployment remains Disabled
through DryRun. Apply uses only the bounded Limited bootstrap described above
before it is enabled after the entire fleet has been verified. Apply updates
Codex and Claude Code with their supported `update`
commands before starting a new Tools or interactive agent, resolves the
authenticated Grok CLI's current provider-default model, and resumes each
verified persistent C-drive worktree at its current branch and HEAD. The
committed branch/HEAD remains a recorded deployment baseline. Recovery never
fetches, checks out, resets, creates a branch, or creates a replacement
worktree.

The explicit runtime choices are:

- Lead: `gpt-6-astra`, Codex mode `xhigh`;
- Tools: `gpt-5.6-terra`, effort `high`;
- RCO1 and RCO2: Claude `sonnet`, effort `max`;
- Fable: Claude `fable`, effort `max`.

Lead's local conversation explicitly preserves its approved full-access/never
workflow through the pinned `existing_interactive` permission setting. This is
visible in the window and does not grant new task, merge or deployment authority.
Its reviewed Codex configuration fingerprint is checked at launcher admission
and again immediately before native dispatch; it is not continuous runtime policy
revocation after the process starts.
Tools keeps workspace-write/never with network access; Codex protected-Git paths
remain protected. Read-only reconciliation never inherits Lead full access.
See `docs/architecture/BRIDGE_OPERATOR_CONVERSATION_V1.md` in the source repository
for pending-work recovery and the difference between transport and completed work.

Durable bridge state, compact lane checkpoints, current Git worktrees, and
pushed savepoints are the resume substrate; a provider transcript is not the
authority. Each lane's first local resume record is
`<worktree>\.codex-audit\wd-current-state.json`. Lanes update it atomically with
`C:\Python\Write-WdLaneCurrentState.ps1` after every bounded slice. Large
Markdown handoffs remain audit history and are read only as fallback when the
compact state is missing, inconsistent, or insufficient for a named historical
fact. A green checkpoint must still be saved with `tools/savepoint.ps1`, because
no launcher can reconstruct bytes that were never durably written before a
power loss.

The dated `WD_CURRENT_REBOOT_STATE_20260725.md` is retained as historical
evidence but is no longer a default startup input. Startup reads the current
pointer, compact lane state, live bridge next action/claims, fleet roles, lane
prompt, and `WD_SWARM_PARALLEL_POLICY_V1.md` before considering old handoffs.

The parallel policy keeps independent work moving on separate axes: Lead owns
core integration, Tools owns tooling/tests/docs, RCO1 and RCO2 perform
independent exact-head reviews, and Fable owns a disjoint producer slice. The
Lead maintains ready work for each available lane. Same-file edits, promotions,
merges, deploys, and exact-head dependencies remain serialized. Tools keeps one
bridge identity but may parallelize read-only discovery and file-disjoint test
processes inside one tick; only its parent consumer claims work and emits bridge
events. Existing evidence is reused only when SHA, relevant files, command,
configuration, and material environment inputs match exactly.

Read-only fleet parallelism/status view after restore:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Python\Get-WdSwarmParallelStatus.ps1
```

It reports compact checkpoint health/age, lane task/status, exact-HEAD match,
pending bridge wake sentinels, runnable lanes, and exact duplicate write-scope
claims. It never acknowledges traffic or mutates bridge/Git state.

Each native interactive Claude lane maintains exactly one lane-specific
five-minute cron backstop plus its current dynamic `ScheduleWakeup` one-shot.
The installed build observed on September 14 exposes **session-only** jobs,
not durable jobs: recreate them on every new session and verify both configured
and actually-fired evidence. A cron-triggered turn alone does not prove that a
dynamic wake fired. Neither mechanism interrupts a running or hung turn.

Keep turns bounded. On a no-op cron, monitor or dynamic-loop turn, confirm the
existing pending one-shot with `CronList` and leave it unchanged. Do not rearm
merely to end a turn: even remaining-time rearming can round the target forward
one minute per call. Schedule only when no one-shot remains or a real scheduling
change requires it, and record the scheduler's confirmed target in compact state.
A recurring backstop and one pending dynamic wake are not duplicate jobs.
After a one-shot actually fires and its slice finishes, choose one new future
eligibility deadline and record the confirmed target. A pending wake that goes
missing before it fires instead recovers its existing confirmed target. If that
target is already due, read the bridge and execute the eligible slice now.
The launcher's current scheduling instruction supersedes legacy role-prompt
self-pacing clauses only; it does not change role permissions or task authority.

An explicitly managed lane uses a launcher-owned runner instead of an interactive
CLI prompt. Without a conversation surface this is `Invoke-WdLaneTurnLoop.ps1`;
the managed Codex Lead's `local_window` selects
`Invoke-WdCodexConversationLoop.ps1` and `Show-WdOperatorConversation.ps1`.
All three scripts are hash-pinned and loaded from the verified byte snapshots.
The conversation window requires an STA PowerShell host, which the fleet
launcher supplies. Each managed Lead turn has a deliberate 3,600-second
wall-clock limit. Turn receipts and local diagnostics are under the worktree's
`.codex-audit/wd-turn-loop/`. It must not also create a competing native cron.
A valid fresh task-blocked checkpoint keeps future wakes/backstops available;
waiting for a peer or CI does not finish that task or disable the lane. Missing
or invalid receipts and ambiguous/crashed turns still require reconciliation
before another model turn.
The default Lead is now `interactive` with `conversation_surface: none`.
It opens the standard Codex terminal at `gpt-6-astra` / `xhigh`; `/model` can
change the model and reasoning level inside that terminal. No custom Lead
window or UI approval watcher starts. Native permissions preserve the previously
approved `danger-full-access` / `never` posture.

When `.codex-audit/wd-turn-loop/conversation.json` records a clean existing Lead
thread, the launcher uses `codex resume <exact-thread-id>` in the canonical
worktree. It never guesses with `--last`. Unresolved managed work blocks this
handoff, and the lane lease is held while the native terminal runs. Previously
delivered initial image/context is not replayed. Codex restores conversation
history in its own terminal. Bridge identity and pinned helpers are established
before native launch; the colored read-only bridge monitor remains separate.
Native startup does not attach the former managed wake consumer. Bridge watcher
sentinels alone do not submit prompts to an idle native Codex terminal.
RCO1, RCO2, Fable and Tools retain their independent sessions.

Do not edit an installed hash-pinned manifest in place. An already-open
interactive Lead is preserved and is not externally resumable through a wake
sentinel. Installing source files does not transform that live session into a
managed one. See `docs/architecture/BRIDGE_WAKE_CONTINUATION_V1.md` and
`docs/architecture/BRIDGE_OPERATOR_CONVERSATION_V1.md` in the repo.
Deployed whole-fleet preflight checks native occupancy for a missing managed
lane before Apply changes other processes. The lane rechecks at launch; this
read-only check does not reserve ownership. Unresolved turn journals are checked
under the runner lease and can still stop a later turn. Fleet restore completion
proves bootstrap identity, not that a managed model turn has completed or that
an owner in termination hold is healthy. Inspect owner state and fresh receipts.
The status view reports configured and observed modes separately. Its
`configured_conversation_surface` is only a next-start setting;
`conversation_control_verified: false` does not assert a working GUI or RPC
channel from a manifest or bootstrap handshake. A retained
interactive Lead remains externally unsupported. Unknown native processes
must first be attributed to their owning sessions and deliberately closed after
saving their work, or relaunched through marked launchers. Do not kill or adopt
them merely to clear a launch guard, and do not delete unresolved turn journals.

### Operator-only inspection and retirement of an unresolved Lead attempt

If the old managed Lead is no longer live but its owner/pending journal blocks
startup, first ask the installed launcher for a byte-inert plan:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Python\start-wd-agent.ps1 -Agent codex-lead-1 -RecoverInteractive -DryRun
```

After reviewing the printed pointer, pending record and 64-hex digest, start the
manual inspection only from an interactive Windows Terminal:

```powershell
powershell -STA -NoProfile -ExecutionPolicy Bypass -File C:\Python\start-wd-agent.ps1 -Agent codex-lead-1 -RecoverInteractive
```

This is a Lead-only, operator-explicit read-only inspection prompt. It verifies
the original managed/local-window/full-access configuration and config baseline,
canonical paths, bundle/CLI pins, a cold lane lease, owner/native inactivity and
the unresolved evidence. It rejects ambiguous PIDs, any live owner/native,
`-NonInteractive`/redirected input, and Codex/Claude/Lead-launcher ancestry. A
signed same-user, same-session Microsoft Windows Terminal is the narrowly allowed
boundary when that terminal's own parent has already exited. This is an advisory
same-user operator boundary plus typed confirmation below, not cryptographic
human attestation. The launcher holds the lane lease for the whole inspection.
It does not replay work, mutate the journal, fabricate a checkpoint, release a
claim, or establish whether an external effect occurred.

If inspection supports abandoning the uncertain attempt, copy the exact printed
digest, record a reason, and run the retirement plan before the real operation:

```powershell
$Digest = '<exact 64-hex review digest>'
$Reason = '<why this uncertain attempt is being abandoned>'
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Python\start-wd-agent.ps1 -Agent codex-lead-1 -RetireManagedAttempt -ReviewedJournalDigest $Digest -RetirementReason $Reason -DryRun
powershell -STA -NoProfile -ExecutionPolicy Bypass -File C:\Python\start-wd-agent.ps1 -Agent codex-lead-1 -RetireManagedAttempt -ReviewedJournalDigest $Digest -RetirementReason $Reason
```

The real retirement command requires an interactive console and typing the
displayed first 12 digest characters. It recomputes the digest while holding the
cold lease, archives the original journal byte-for-byte to
`<worktree>\.codex-audit\wd-retired-conversations\<digest>\journal`, writes a
verified sibling `retirement-manifest.json`, and moves the runtime owner pointer
to sibling `runtime-owner-pointer.json` **last**. A failure before that final move
restores the journal when possible and leaves the original pointer blocking.
Existing/partial archives are never overwritten. The operation records an
operator-abandoned uncertain attempt; it does not assert success, prove effects
were undone, alter compact state, release/transfer bridge claims, or mark a bridge
task complete.

After successful retirement, use the ordinary fleet DryRun/Apply flow to create a
genuinely new managed Lead thread. It starts PAUSED and does not automatically
inherit the retired transcript or record. Before directing further work, paste
the printed manifest path, digest and reason into that window as advisory context.
This explicit human handoff is not new authority and does not make the old result
verified.

Before each lane invokes its model, the launcher verifies
`WaggleDanceSwarmAi.png` by its pinned hash and delivers that exact image once
in the lane session's initial model turn. Codex lanes receive it as the native
initial image input; Claude lanes must use their visual Read tool on the same
PNG before reading the bridge. The image remains the primary north-star and is
not replaced by a prose interpretation. It is direction, not evidence of a
current capability, and grants no capability or authority.

Each lane also writes one `target_state_manifested` status event and one
unaddressed `append_canary` for that reboot run through the manifest-hashed
writer. The canary must complete within five seconds. The launcher preserves
the frozen canonical prefix and requires the pre-existing spool inventory to
remain byte-exact before it enables and demand-starts `WD-Supervisor`. Failure
to append either event prevents that lane from launching.

The Grok provider default is the authoritative, non-hard-coded choice available
to this account after the CLI update. The resolver verifies that it occurs
exactly once in the provider's available-model list and records both the model
and exact high-effort invocation. It does not guess a “strongest” model from
version-like names.

The resolved Grok model and exact invocation examples are written to:

- `C:\Python\WD_GROK_MODEL_CURRENT.json`
- `C:\Python\WD_GROK_MODEL_CURRENT.md`

## Pinned bridge code package

Every reboot bundle carries the bridge communication code that lanes execute
after launch, so no session depends on a worktree-relative or mutable helper:

- `tools-bootstrap\.agent-bridge\bin\*` (all bridge PowerShell helpers);
- `tools-bootstrap\tools\*.py`, `tools-bootstrap\waggledance\core\*.py` and
  `tools-bootstrap\configs\*.json` listed in `bridge-code-files.json`;
- `tools-bootstrap\python-wheels\*.whl`, the exact dependency closure
  (pydantic and its four dependencies) pinned by version and sha256;
- `tools-bootstrap\python-site\`, those wheels extracted without bytecode.

Every file is hashed in `deployment-manifest.json`. `BridgeCodeContext.ps1`
re-verifies the package before each lane or Tools launch: a missing file, a
hash mismatch, an unlisted file, a `__pycache__` directory or a bundle that
predates the package fails closed. There is no fallback to local copies.

The interpreter is the per-user `bridge_python.executable` from
`wd-fleet.json` (the same trust rules as the Tools lane: absolute `.exe`
under the per-user `Programs\Python` root, never WindowsApps, no reparse
points, sha256 recorded). Only its standard library is used; every
third-party import resolves inside `python-site`, and a launch-time import
smoke proves it.

The launcher exports only these discovery variables to the CLI process, and
the Tools consumer exports them to each `codex exec` tick. No `PYTHONPATH`,
`PYTHONSAFEPATH` or `PYTHONNOUSERSITE` reaches the model shell, so the task
worktree's own `waggledance` and `tools` imports, tests and development are
unaffected:

| variable | value |
|---|---|
| `WD_BRIDGE_CODE_ROOT` | `<bundle>\tools-bootstrap` |
| `WD_BRIDGE_BIN` | `<bundle>\tools-bootstrap\.agent-bridge\bin` |
| `WD_BRIDGE_PYTHON`, `WD_BRIDGE_PYTHON_SHA256` | pinned interpreter and its hash |
| `WD_BRIDGE_PYTHON_SITE` | `<bundle>\tools-bootstrap\python-site` |
| `WD_BRIDGE_GENERATION` | the bundle source commit |
| `WD_BRIDGE_PYTHON_WRAPPER` | `<bundle>\Invoke-WdBridgePython.ps1` |
| `WD_BRIDGE_BUNDLE_ROOT`, `WD_BRIDGE_RUNTIME_ROOT` | bundle directory; bridge data root |

Invoke bridge helpers through those variables, never through a
worktree-relative `.agent-bridge\bin` copy:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:WD_BRIDGE_BIN\Get-BridgeNextAction.ps1" -Agent <agent> -Json
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:WD_BRIDGE_BIN\Read-AgentBridge.ps1" -Compact
& "$env:WD_BRIDGE_PYTHON_WRAPPER" tools/bridge_next_action.py --agent <agent> --json
```

Python isolation exists only inside `Invoke-WdBridgePython.ps1`: it verifies
the tool and definition hashes, then runs the tool with `-S -B`, `PYTHONPATH`
set to the pinned code root plus `python-site`, `PYTHONSAFEPATH`,
`PYTHONNOUSERSITE` and `PYTHONDONTWRITEBYTECODE`, restores the process
environment afterwards and preserves the caller's working directory. Global
and user site-packages are never on its import path.
`Read-AgentBridge.ps1 -Compact` uses the wrapper automatically. The wrapper
keeps the tool's output on the pipeline and publishes its exit code as
`$LASTEXITCODE`; from a separate process call it through `-Command` and
forward that code. The
operator-owned role prompts under `C:\Python\wd-agent-prompts\` should be
migrated to the same forms. Git, build, test and merge commands keep the lane
worktree as their cwd; the pinned code root is never a task repository.

Staging a new generation without touching the live fleet:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ops\windows\reboot\Deploy-WdRebootBundle.ps1 -StageOnly
```

`-StageOnly` materializes the pushed commit, downloads the pinned wheels with
`pip download --require-hashes` (or reads them from `-WheelSource <dir>` with
the same hash check), extracts the site with `--no-compile`, verifies the
commit-addressed directory recursively and returns before any machine
wrapper, data copy, state pointer, Grok resolve or task registration is
written. The running supervisor, its watchers and every live session keep
the previous generation. Activation is a separate, coordinated cold switch:
run the installer without `-StageOnly` only after the supervisor task has
been disabled and its current invocation has exited naturally, then verify
and re-enable. The installer's rollback is exception-only (a failed machine
write restores the timestamped backup under `C:\Python\wd-reboot-backups`);
it is not crash-atomic, so keep that backup inventory until the switch is
verified.

## Source and integrity

### Grok: passive recovery, lead-requested consultations only

On the first upgrade, stage the committed bundle, then explicitly run its
`Initialize-WdGrokRecovery.ps1 -Apply` before activation. This refuses active
Grok invocations, backs up legacy task definitions and scripts, disables their
schedules, retires bypass/reset entry points, and initializes a conservative
one-hour hold only if no hourly state exists. Existing reports and budget state
are preserved. Normal installation checks this migration before switching
machine pointers; subsequent startup performs only passive validation.

The same fleet startup validates Grok's persistent role and hourly state without
calling a model. Lead invokes `C:\Python\Invoke-WdGrok.ps1 -Status` to inspect the
previous task/report and next eligible time, or supplies `-PromptPath` and
`-TaskId` for one evidence-based advisory consultation. This is not a persistent
CLI conversation: the previous bounded report and current saved lead work state
are included as context. No tool execution, subagents, automatic research,
merge/deploy authority, worktree reset or retry is granted.

All attempted consultations share one OS lock and one durable hourly reservation
under `C:\Python\grok-scout-reports`. Failures/timeouts consume the hour too.
Initial migration conservatively holds one hour because old scripts did not
reliably record failed attempts. Legacy autonomous Grok scheduled tasks must
remain disabled. Direct CLI/API calls outside this controlled entry point are
not governed by its budget and must not be used by the fleet.

Model metadata is refreshed by the existing startup resolver; an expired or
unavailable model/authentication can still block a consultation. No wrapper can
guarantee provider availability or override the hourly limit.

The Git repository is the only source of truth. A pushed commit is installed
into `C:\Python\wd-reboot-bundles\<full-commit-sha>`. Machine-local
`start-wd-*.ps1` files are small, hash-checking wrappers only.

Current pointers:

- `C:\Python\WD_REBOOT_STATE_CURRENT.json`
- `C:\Python\WD_REBOOT_STATE_CURRENT.md`
- `C:\Python\WD_REBOOT_INTEGRITY_CURRENT.json`
- `C:\Python\WD_REBOOT_INTEGRITY_CURRENT.sha256`

The older `WD_REBOOT_INTEGRITY_20260725.sha256` and dated reboot-state file are
historical records. They do not override a newer handoff or the live bridge.

## Authority and safety

Startup state precedence is:

1. live bridge state, read without acknowledging stale events;
2. a valid compact per-lane checkpoint;
3. the current reboot pointer, fleet roles, lane prompt, and parallel policy;
4. newer fleet and per-agent Markdown handoffs as fallback;
5. dated snapshots as historical evidence only.

Recovery grants no merge, deploy, signature, canary, runtime-authority, or
`claim_safe` permission. `WD-BridgeMergeDriverStandingOneShot` is deliberately
disabled. Neither the launcher nor the supervisor contains an enable path for
it.

Watcher replacement creates a durable identity- and generation-bound marker
before the first stop. The marker is removed only after all five watchers pass
post-reconcile verification. If a marker remains, stop and inspect it; do not
delete it merely to make the launcher proceed. A pre-existing admission,
marker, or mutex conflict blocks the planned watcher and Tools mutations, but
the supervisor may still disable and stop a merge-driver task to preserve the
dominant HOLD invariant. A stop, launch, or post-verification failure can leave
a partial roll-forward state plus its durable marker; the next step is
inspection, not blind marker deletion.

The launcher is roll-forward, not process-transactional. If a later CLI, Grok,
handshake, or terminal-launch step fails after the supervisor has converged the
watchers and Tools, leave the verified helpers running, fix the reported cause,
and repeat `-DryRun` followed by `-Apply`. Do not bulk-replay bridge spool files
as part of reboot recovery.

`-DryRun` performs read-only probes only: no CLI update, cache write, bridge
event, task mutation, process launch, report write, checkout, or fetch.
