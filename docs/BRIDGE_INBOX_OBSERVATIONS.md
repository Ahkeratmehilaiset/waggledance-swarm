# Agent inbox filtering and fleet observations

`Monitor-AgentBridge.ps1 -TargetedOnly -IncludeWakeRequests` is the agent inbox
mode used by native Claude sessions. It applies the same wake eligibility policy
as the watcher: suppress ACK/liveness and explicitly informational unbound
notices, but retain requests and bound late replies or corrections. A bounded
4096-event hash set suppresses identical events within one monitor process;
changing request IDs, payloads or other event fields remains visible. This is
noise reduction, not an exactly-once task execution guarantee. Cursor persistence
prevents historical replay; the hash set is not persisted across restarts.

Other monitor modes remain suitable for human observation and retain their
informational rows. The color log viewer is unaffected.

## Read-only status

`Get-WdSwarmParallelStatus.ps1` separates these observations:

| Field | Meaning |
| --- | --- |
| `sentinel_present` | The wake marker exists. It does not prove pending work. |
| `wake_pending: true` | An eligible addressed event was read after the agent inbox cursor. |
| `wake_pending: false` | The bounded delta reached its observed EOF without an eligible unread event. |
| `wake_pending: null` | Delivery state is unknown: missing, legacy, filtered, invalid or incomplete cursor/read evidence. |
| `wake_observation` | Source, observation time, reason and delivery state. No cursor or marker is changed. |
| `health_observation` | One projection of separately scoped process, identity, transport and last-answer observations. |

`summary.pending_wakes` counts only positive observations. Always read it together
with `summary.unknown_wake_observations`; zero pending with unknown lanes does not
mean that the whole fleet has no pending work. `summary.wake_sentinels_present`
counts marker files separately. Consumers that previously treated `wake_pending`
as an unconditional boolean must handle null explicitly.

Inbox evidence requires a cursor recording the correct agent, unfiltered sender,
targeted mode, included wake requests and `delivery_scope=agent_inbox`. Older
monitor cursors become eligible after the updated monitor saves its next cursor.
An explicit lane-local `-StatePath` is resolved from a unique live invocation of
the selected Monitor script. Ambiguous invocations or paths outside the lane's
audit directory and bridge runtime remain unknown; no filename glob guesses the
latest cursor.
Native Codex queue delivery uses a different transport; lack of this Monitor
cursor remains unknown and is not inferred from a missing sentinel. The existing
relay observation still reports known blocked states and their errors separately.

Checks are bounded to 1200 rows and 1 MiB per cursor delta. Replacement,
truncation, invalid data and incomplete reads cannot establish an empty inbox.
Concurrent appends after the sampled EOF belong to a later observation.

A cursor records delivery through the monitor, not that the model processed the
message, completed its task or reported to the operator. Similarly a live launcher
and handshake are weaker evidence than a verified native conversation. The status
command preserves these distinctions and does not restart or wake agents.
