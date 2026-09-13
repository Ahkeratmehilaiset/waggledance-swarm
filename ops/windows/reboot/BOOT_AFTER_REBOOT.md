# WaggleDance: reboot recovery

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

Each elevated `-Auto` run keeps a transcript under
`C:\Python\wd-reboot-runtime\elevated-auto`. If the elevated process fails, the
parent PowerShell prints the transcript tail and its exact path.

Its non-mutating verification mode is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Python\start-wd-all.ps1 -DryRun
```

For a manual two-step recovery, run `-DryRun` and then `-Apply`. With no mode
switch the launcher defaults to byte-inert DryRun. After a successful restore,
leave its four Windows Terminal tabs open. The fifth, headless Tools
lane and exactly five real-time bridge watchers are reconciled by the same
command through `WD-Supervisor`.

The first headless Tools tick runs before its readiness record is published and
can take several minutes. During that bounded wait, `-Auto` prints progress
every 30 seconds. A readiness record that is present but not attested is a
launcher/process-identity problem, not a reason to wait silently.

The elevated restore never launches the five bridge watchers or headless Tools
directly. It demand-starts the exact `RunLevel=Limited` WD-Supervisor task once,
waits for that scheduled path to finish successfully, and returns the task to
Disabled/HOLD while the interactive lanes are restored. This keeps every
supervisor-owned process visible to later Limited supervisor runs and prevents
an elevated/Limited duplicate-generation race. The task is enabled permanently
only after the complete fleet and bridge baseline have passed verification.

After the interactive `codex-lead-1` lane has completed its bridge-bootstrap
handshake, the restore also reconciles exactly one separate Codex prompt-watcher
window. It targets only the terminal title `codex-lead-1` and runs the bundled,
hash-verified `Watch-CodexPrompts.ps1` with `-AllowAll -NoAllNighter`. This is
intentionally dangerous: `-AllowAll` bypasses both that script's command
allowlist and denylist and can approve any Codex command prompt it recognizes
after the desktop-idle guard permits input. Keep the prompt-watcher window open
only while this unattended behavior is intended. Claude lanes already use
`--dangerously-skip-permissions`, and headless Tools uses approval policy
`never`; neither receives a UI prompt watcher.

DryRun verifies the prompt-watcher script and reports whether it would keep or
launch the single Lead watcher. A non-canonical Lead watcher or more than one
watcher targeting `codex-lead-1` is an ambiguous conflict and stops recovery
before CLI updates or process launches. The prompt watcher is separate from the
five supervisor-managed real-time bridge watchers. Failure to materialize a
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

- Lead: `gpt-5.6-sol`, Codex mode `ultra`;
- Tools: `gpt-5.6-terra`, effort `high`;
- RCO1 and RCO2: Claude `sonnet`, effort `max`;
- Fable: Claude `fable`, effort `max`.

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

Each Claude lane maintains exactly one lane-specific durable five-minute cron
backstop and still calls `ScheduleWakeup` on every dynamic `/loop` turn. The
durable cron re-enters compact-state/bridge processing after a missed dynamic
wakeup and is refreshed before Claude's seven-day durable-job expiry. It never
authorizes a duplicate claim.

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
