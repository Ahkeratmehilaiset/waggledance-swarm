# Independent Lead and Tools conversations v1

The operator must be able to direct the Lead while it works, not just watch
bridge messages. The next-start Lead configuration combines `turn_mode: managed`
and `conversation_surface: local_window`. This document describes source behavior
and acceptance requirements, not proof of installation or a successful live turn.

## Context and control

One launcher owns one agent app-server subprocess and one persistent conversation
thread. Human messages and automatic bridge-wake turns use that same agent's thread.
Lead and Tools use separate windows, OS leases, verified worktrees and saved
thread identities. RCO1, RCO2 and Fable keep their native interactive windows.
The Tools window belongs to the existing supervisor-owned Tools parent; enabling
it replaces that parent's headless consumer loop, never adds a second consumer.
RCO1, RCO2, Fable and Tools retain their own sessions and context. The GUI does
not combine their histories or create another Lead. Several agent contexts can
hold different information; that is not an enlarged context window for one model.
Bridge handoffs and compact checkpoints carry the information needed between
agents and after restart. Provider history is useful context, not task authority.

The local WinForms surface has a transcript, a composer, attachment selection,
interrupt, and a separate automation toggle. While a turn is active, sending
means steering that exact turn, not queueing another model execution. After an
intentional interrupt, an explicit message can continue the same thread without
implicitly enabling automatic wake turns. The UI labels automation separately
from conversational continuation. A read-only bridge display is supplementary;
it does not replace streamed Lead replies or grant a bridge-writing identity.

The [Codex app-server protocol](https://learn.chatgpt.com/docs/app-server) provides
stdio JSON-RPC, `initialize`/`initialized`, `thread/start`, `thread/resume`,
`turn/start`, `turn/steer` with `expectedTurnId`, and `turn/interrupt`.
Committed tests use deterministic fake-native protocol fixtures. Separately,
development audit probes validated captured requests against the schema generated
by the installed CLI (`codex-cli 0.153.4`); those generated schemas are audit
artifacts, not a committed test dependency. A handshake-only probe proves transport
compatibility, not model execution, image delivery or workflow completion.

## Ownership and trust

The GUI and action queue are inside the launcher-owned backend process. Its
contained native child communicates through anonymous stdin/stdout handles;
there is no listening web port, discoverable named-pipe RPC service, or arbitrary
RPC entry box. Actions are a closed set and bind to the current owner epoch and
observed turn. Stale steering is rejected visibly, never retargeted or silently
retried as a new turn. User text is input, not a method name or executable shell.

Explicit pins remain Lead `gpt-5.6-sol` / `ultra` and Tools `gpt-5.6-terra` / `high`.
The default backend policy is `workspace-write` / approval `never`, no network,
with its verified worktree and bridge runtime writable. Tools explicitly enables
network access, without extra shared Git or sibling-worktree write roots.

The hash-pinned Lead-only `conversation_permissions.posture: existing_interactive`
is an explicit compatibility setting for the already-approved full-access/never
Lead workflow. It requests `danger-full-access` for ordinary Lead turns, requires
network enabled and no misleading additional-root list, and is rejected for
Tools. The UI warns about full access; owner records identify the actual posture.
This is not a narrowed filesystem sandbox. Saved conversation posture must match
the new launch; a missing old field means workspace-write, never an implicit
upgrade. Independent review is required before activating this compatibility mode.
Every explicit reconciliation turn remains read-only with network disabled.
Startup verifies the reviewed global Codex configuration path, security SHA-256 and
top-level full-access/never declarations, then rechecks before native dispatch.
A changed `CODEX_HOME`, changed security fingerprint or any unreviewed project/ancestor
`.codex/config.toml` blocks this mode; it does not override tightened operator
settings. Only the path/hashes and permission values enter the handshake, not the
configuration contents. The security fingerprint ignores only the exact known
cosmetic `[notice]` and `[tui.model_availability_nux]` sections and normalizes CRLF;
all other settings, including plugins, MCP, providers, shell policy and project
trust entries, remain covered. Ambiguous multiline syntax is refused. The whole
file hash is recorded for audit but not used to block cosmetic counter updates.
After a deliberate security-relevant config change, review and release a
new baseline through the normal workflow; never rewrite an installed manifest.
The requested unattended mode starts new conversations with automation enabled;
automatic turns use the same full access as ordinary Lead work. Pause/interrupt
is explicit and persists across restart. A Job Object contains process lifetime,
not filesystem or network access; it is not an external sandbox.
Existing authenticated plugin connections remain technically reachable under
full access. This setting does not authorize using mail, design or other external
accounts outside the operator's task; those permissions are not a UI sandbox.

The reason is functional, not cosmetic: Codex workspace-write protects `.git`
and resolved linked-worktree Git directories. Adding the parent directory did
not remove the observed deny rule and is not a verified fix for commits/releases.
Tools therefore must not claim protected-Git writes work merely because network
access is enabled; such work needs the existing authorized Lead workflow.
See [official sandbox rules](https://learn.chatgpt.com/docs/agent-approvals-security).

Network access is not a Git-only command filter. There is no new git/gh broker,
subcommand allowlist, or per-ref isolation. Full-access Lead can technically reach
other writable paths; task claims and scope remain binding instructions. Existing
guarded Git workflow, PR-only rules, exact-head approvals and remote branch
protections remain separate requirements, not controls supplied by this UI.
The UI does not automatically approve requests or change the model's permissions.
Unsupported server approval requests receive an explicit rejection. Supported
user-input questions are correlated to their request and turn, not treated as
permission escalation. Image attachments require a caption and an idle turn;
active steering is text-only. The UI permits at most four local images, 10 MiB
per image and 20 MiB combined, with local-path checks before dispatch. Filename
extensions are checked, not a full image decoder; selected images are sent to the
model provider, not kept strictly local. Other
files may be named in the message for the Lead to inspect within its task scope;
the GUI does not upload those files. Invalid input or rejected steering retains
the draft; only accepted, unchanged text is cleared. No attachment bytes are
copied into bridge events. Model tool calls remain governed by the existing scope.

The GUI never forwards an operator message to both RPC and the bridge. The Lead
decides and records necessary coordination through its ordinary bridge tools.
Bridge text cannot directly enqueue a UI action or select an RPC method.

The guarded launcher verifies CLI identity, occupancy and bundle integrity, then
loads the shared runner, GUI and conversation backend from verified byte snapshots.
Standalone production entry is refused. Existing interactive Lead or unexplained
native occupancy blocks a new managed owner; it is not permission to adopt or kill
that process. The OS lane lease remains held through native job termination.
This lease prevents another runner owner; it does not renew or validate a bridge
task claim on behalf of an idle model.

## Lifecycle and recovery requirements

Only one model turn is active. Wakes arriving during a human turn coalesce for
later idle processing. Pending questions, paused automation and reconciliation
holds prohibit automatic execution. Native turn completion still needs a fresh
turn-bound receipt and compact checkpoint before it is a checkpointed workflow
outcome; the GUI transcript is not a substitute for either. A human-originated
pure chat reply may finish without a workflow checkpoint only when no tool or
unknown item type was observed. This never proves task completion. Automatic
turns and any effectful or unknown-item turn still require checkpoint evidence.
The transcript marks message delivery as pending, accepted, rejected or unknown.
Interrupt remains latched until the exact turn's terminal event, not just RPC ACK.

The app-server is persistent across turns, unlike the older per-turn CLI runner.
A native completed/interrupted event does not independently prove tool-descendant
drain. Shutdown or safety termination contains and drains the whole owned job
before releasing the lane lease. An unconfirmed shutdown retains the lease and a
visible hold. A backend crash kills its contained app-server; the design does not
leave an independently running server to rediscover or adopt on restart.

RPC intent is durably journaled before sending. Unknown dispatch or missing
completion is not automatically replayed. A confirmed interrupt has its own
evidence and pauses automation; it is never fabricated as a successful checkpoint.
An interrupt without native confirmation remains unresolved. Paused automation
must persist across restart. Only the thread recorded by this owner may be resumed;
never search provider history for a plausible foreign thread. A clean restart can
resume that recorded thread; unresolved pending work first requires reconciliation.
Resume explicitly uses `excludeTurns: true`: model context is retained, but the
GUI does not reload the previous transcript. This avoids making a long valid
session fail recovery because full history exceeds a single-message buffer.
The window explains this distinction; it does not silently create a fresh thread
or claim that old display text has been restored. Paginated history browsing is
not implemented in this version.

Closing the window closes its owner, not merely a detachable view. Reopening
therefore goes through guarded startup and recovery; it is not live attachment to
the retained interactive session. The initial north-star PNG remains the exact
hash-pinned image. Initial context is marked delivered only after native turn
acceptance, not merely after creating an empty thread; interruption before first
acceptance must not silently skip it on restart.

### Missing-checkpoint recovery

A known completed native turn with missing/invalid checkpoint evidence pauses
automation and leaves its original pending journal intact. While its contained
server is still alive and idle, the explicit **Reconcile** action can request a
read-only inspection in that same owned thread. Ordinary Send, Continue and
automation stay disabled. This action does not replay commands, manufacture a
receipt, clear pending evidence, or mark the task complete. Its result is separate
reconciliation evidence. Read-only is the native local sandbox plus an explicit
no-external-mutation instruction, not an independent permission layer for MCP.

Record the exact original owner/session/generation/thread/turn and pending path;
compare native terminal evidence, bridge task/claims, compact checkpoint and Git
status before deciding what actually completed. Preserve unresolved files. An
unknown dispatch, crash or missing native terminal is a hard hold, not eligible
for this same-live-thread action. Do not delete pending files or edit a receipt to
unblock restart. Normal write/automatic operation requires an independently
verified recovery decision; this version does not automate that promotion.
Unconfirmed dispatch is never assumed not to have happened. For a turn that used
GitHub or another external system, inspect remote state too: a push may succeed
before local acknowledgement is lost. Unknown-effect work stays on hold; neither
RPC timeout nor a new message authorizes replay, and remote idempotency is not
claimed by the journal.

Local journaling and display buffers are bounded. Unresolved evidence is retained
for reconciliation; do not delete it simply to clear a guard. Native provider
transcripts are outside local journal retention and are not guaranteed unlimited.

## Acceptance and rollout

Required deterministic tests cover both supported Windows PowerShell hosts:
verified launcher dispatch and STA mode; native-job stdio lifecycle; exact-turn
steer and late responses; interrupt and manual continuation; durable paused
restart; missing checkpoint and disconnect holds; lease conflict; wake coalescing;
question-answer correlation; unsupported approval rejection; attachment bounds;
and GUI action/status behavior, including long-history resume without full-history
hydration. Fake-native tests are not live-provider evidence.

Before activation, also verify package hashes and exact-head CI, independent
review, and a bounded real-provider acceptance run. Record actual thread identity,
streaming, steering, interruption, attachment delivery and recovery separately.
Configuration and bootstrap handshakes alone never set
`conversation_control_verified` to true.

Tools `wd.tools-consumer-ready.v2` records UI transport separately from native
checkpoint progress. It binds wrapper and native PID/start times, generation,
session and thread. `transport_ready` is not model progress, useful work, or task
completion. Latest-turn checkpoint state and last verified checkpoint are distinct.
Legacy headless mode retains readiness v1; a v1 record cannot attest a v2 window.
The separate colored, read-only bridge monitor remains on by default after a
successful fleet restore (`-NoBridgeConversation` opts out). It includes bridge
events from all emitters; it does not itself make Grok an always-running agent.

Do not replace the current interactive Lead while it is running. Keep all peers
and existing work intact while validating the replacement. Stage the committed
bundle separately from installation. A controlled new startup, live acceptance,
and a recorded rollback route are required before treating this window as the
operator's replacement control surface. Never edit an installed hash-pinned
manifest in place or claim that source installation converted a live session.
