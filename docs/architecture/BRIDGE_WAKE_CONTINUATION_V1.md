# Bridge wake continuation v1

This contract separates a delivered notification from an executed model turn.
It does not grant merge, deployment, release, signature or broader filesystem
authority. Installed behavior must be established from the installed bundle and
live evidence, not inferred from the presence of this document.

## Confirmed recovery gaps

The September 14 recovery exposed two independent issues:

1. The Python next-action selector treated `message/received` as a substantive
   answer. An agent could acknowledge a task, then have the selector hide the
   unfinished request. The PowerShell classifier already excluded receipt ACKs.
2. `Watch-Bridge.ps1` writes `wake_<agent>`; `Test-BridgeWake.ps1` reads/removes
   that signal. Neither starts a model turn. An open interactive Codex session
   whose turn has ended is not made autonomous by that sentinel.

The selector repair excludes receipt ACKs and infrastructure traffic from
completion, distinguishes a requester's terminal closeout from reminders, and
keeps `done/request` request-like. One recipient's answer does not finish work
for every other recipient. Existing exact-task and age rules remain in force.

## Scheduling ownership

| Lane/mode | Turn owner | Meaning of a wake |
| --- | --- | --- |
| Tools, existing supervised consumer | Its one canonical consumer | Run a bounded tick through the existing backend. |
| Tools with `local_window` | The same supervisor-owned Tools parent | Run one bounded turn in its independent persistent thread; no additional headless consumer. |
| Existing interactive Claude RCO1/RCO2/Fable | Native session scheduler | Native wake/backstop reads current bridge state; do not launch a twin. |
| Existing interactive Codex Lead | Existing interactive session | Notification only; no verified external same-session turn adapter. |
| Managed startup without a conversation surface | Launcher-owned turn loop | Start one owned, bounded CLI child after validating occupancy. |
| Managed Codex Lead with `local_window` | Launcher-owned conversation loop | Start a bounded turn in its one owned app-server thread when idle and automation is enabled. |

The managed runner must own a lane from startup. It must not take over an
already-open interactive session, use `SendKeys`, inject approval responses, or
run concurrent `codex exec resume` against a live interactive conversation.
Preserving that conversation and changing its process ownership are different
operations. A retained legacy interactive session remains visibly unsupported
for external turn scheduling until a deliberate new managed startup.

Native Claude scheduling should retain exactly one recurring lane backstop and
the current dynamic one-shot wake. Those are two different jobs, not duplicate
backstops. The observed installed build offers session-only jobs, not durable
persistence. Configuration and observed firing are separate evidence: a cron or
Monitor turn does not prove a dynamic wake fired. Confirm an already-pending
one-shot with `CronList` on no-op ticks and retain it without rearming. Relative
rearming can round its target forward even when remaining time is calculated;
a fresh fixed delay on every cron tick can indefinitely postpone it. Record
confirmed scheduler targets, not estimated placeholder wake times. Recreate native
backstops after a session restart; a session job is not a Windows service.
After a one-shot has actually fired and its bounded slice has finished, select
one new future eligibility deadline from the current task and record the
scheduler-confirmed target. If a pending one-shot disappears before firing,
recover its previously confirmed target instead; if that target is due, read
the bridge and perform the eligible bounded slice now. Do not reuse a fired
deadline as a permanent prohibition on future scheduling.
An explicitly managed Claude lane must not also create a competing native cron.
The current launcher's scheduling instruction supersedes only legacy self-pacing
sections of external role prompts (including mandatory every-turn rearming and
durable-cron claims). It never supersedes role permissions or task authority.

The operator-requested release configures the next Lead startup as
`turn_mode: managed` with `conversation_surface: local_window`; native Claude
lanes retain `interactive`; Tools selects its own `local_window` inside the
existing supervisor-owned parent, replacing the headless tick loop. This takes
effect only after validation and deployment of the matching bundle. The new Lead
has a conversational control window with streamed replies, active-turn steering,
interrupt and separate automation control. It is not merely a lifecycle display.
Peer sessions remain separate; extra views do not multiply one thread's context.
See [the conversation contract](BRIDGE_OPERATOR_CONVERSATION_V1.md) for the
app-server lifecycle, recovery and evidence boundaries. The fleet launcher does not create a
UI approval watcher for managed Lead. An existing approval watcher blocks that
startup until a controlled handoff removes it; it is never automatically killed.
Never modify an installed hash-pinned manifest to switch modes in place.

The managed loop consumes the existing supervisor-owned `Watch-Bridge.ps1`
sentinel for its lane. It does not start a second watcher. A direct launcher
invocation without the supervisor's watcher still has the bounded backstop,
but does not provide real-time event delivery by itself.

## Managed turn contract

The launcher verifies the packaged runner, CLI executable, worktree membership,
model/effort pins and installed generation before execution. Standalone script
entry is refused; production execution goes through that guarded launcher.
Existing live sessions are
preserved; missing or ambiguous ownership evidence is not permission to launch.
The managed launch guard checks one process snapshot and validates native CLI
ancestry and process creation times against known lane launchers, including the
existing Tools consumer. An unattributable native CLI blocks a new managed
launch; the guard does not adopt or terminate it. This is conservative occupancy
checking, not proof of an unknown session's identity.

Resolve unknown occupancy by identifying the owning window/session, saving its
work, and closing it deliberately, or relaunching it through its marked lane
launcher after shutdown. Do not infer ownership from a process name, remove an
owner journal to force admission, or kill unrelated native sessions. A reboot
may remove processes, but unresolved turn evidence still requires reconciliation.
The status view distinguishes configured mode from observed mode and wake
support. A retained interactive Lead stays externally unsupported even when
the next-start configuration says managed; configuration is not a live turn.

Managed Claude requires the guarded launcher to explicitly pass its existing
interactive permission posture. The opted-in managed path uses the same CLI
permission setting as the already-approved interactive path; absent that
explicit posture the runner refuses before creating turn files or launching a
child. Role, task, claim and promotion limits still apply. This is not a new
filesystem sandbox. Codex defaults to workspace-write/never. The conversation
configuration explicitly selects the reviewed Lead-only `existing_interactive`
full-access compatibility posture; Tools stays workspace-write. Missing or changed
saved posture never silently upgrades a thread. Fake-child
tests establish argument and receipt behavior, not real model willingness or
successful live activation.

The loop has one OS-backed owner lease per runtime/lane and records the owner's
PID, process start time, session and generation. PID presence alone is not
identity. A native child is contained in its own Windows Job Object; timeout or
runner failure must not leave child workers writing after ownership is released.
The execution deadline initiates contained termination; it does not guarantee a
bounded shutdown. If Windows never confirms job drain, the lease stays held and
no new turn/backstop runs until verified recovery. Never release ownership early
to hide that safety hold. The owner status files remain fixed-size, though the
termination wait can repeatedly publish status.
The runtime-root owner pointer also identifies the previous worktree journal.
A new worktree must not hide an unresolved prior turn after its process exits.
Recovery checks that pointer under the same lane lease before starting work.

For the per-turn CLI runner, each turn follows:

`pending wake → owned start → bounded CLI turn → fresh checkpoint + receipt → result`

The wake is retained separately while a turn executes. New arrivals remain
pending for a later turn. Repeated notifications coalesce; they do not spawn one
worker per event. A bounded backstop can re-read the queue after missed signals.
An ambiguous/crashed turn is reported as blocked and must not be blindly replayed.

The model receives a fixed local continuation instruction. It reads bridge data
as task context, never as executable shell text or new authority. The north-star
PNG is delivered on the initial turn only; later turns recover from compact lane
state and current bridge evidence rather than repeatedly loading the image.

Checkpointed model-turn completion requires a fresh lane checkpoint and a
structured receipt bound to the exact turn, lane, session, generation and task.
An independently drained turn with a valid fresh `blocked` receipt/checkpoint
is a known task-blocked outcome, not an ambiguous process failure. Checkpoint
that turn and keep the lane wake/backstop available to re-read new bridge state.
Preserve its blocked task status; waiting for a peer or CI is not task completion.
Protocol errors, missing receipts, invalid checkpoints, timeouts and crashed
turns still retain unresolved evidence and stop automatic replay.
This is not independent verification that a requested workflow is complete;
that still requires the canonical recipient response and applicable gates.
The loop's own journal is separate
from the model-owned `wd-current-state.json`. A successful CLI exit, a process
PID, an ACK, a heartbeat or deletion of the wake file is not task completion.

The conversational runner instead retains one contained app-server process
across turns in the same thread. Native turn completion is not process exit or
proof that every tool descendant has drained. Its separate contract specifies
turn-bound receipts, explicit interrupt evidence and whole-job shutdown before
releasing ownership. Do not describe the persistent backend as a fresh drained
CLI child on every turn.

Blocked outcomes must name the cause: unsupported live interactive ownership,
missing backend, held lease, timeout, surviving child, missing or invalid receipt,
or invalid checkpoint. Keep diagnostics bounded and preserve actionable evidence.
Retention covers only successful runner-owned turn artifacts. Unresolved
evidence is retained. The output budget is a polled stop threshold, not a hard
disk quota; native CLI session histories are outside this retention policy.

## Verification and rollout

First test with deterministic local fake CLI children: successful checkpoint and
receipt, ACK-only output, stale checkpoint, wrong identity, concurrent owner,
wake during an active turn, timeout containment and surviving descendants. Test
all supported lane identities without invoking paid models or touching live data.

Then verify launcher dry-run and package integrity. A new source implementation
does not update an existing installed bundle. Live verification is a separate,
controlled step: prove a real wake, one real turn and its checkpoint/result. For
native Claude sessions, obtain configured and actually-fired backstop evidence
from each session. For the retained interactive Lead, verify preservation and
the explicit unsupported state; do not call that successful automatic waking.

Keep the ACK lifecycle fix and the process-runner change independently reviewable.
Neither may alter merge-driver HOLD or turn a review into deployment authority.
