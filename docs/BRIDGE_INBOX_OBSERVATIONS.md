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

## Request inventory: discovery, not answer status

`Get-BridgeRequestInventory.ps1` lists one requester's own request rows. It never
decides answer state: each listed request carries `answer_state=not_evaluated`,
so discovery is never answered, processed or completed status. Resolve an ID with
`Get-BridgeReplySnapshot.ps1`.

By default the getter fails closed, with no page, when an own request-like row
that the reply index kept has a conflicting or malformed author binding or
request_id (a non-string id, or one outside `^[A-Za-z0-9._:-]{1,128}$`), and
when one immutable request ID has conflicting content or digest. The installed
baseline `8a7576af` (getter `e1005fe2`) has this default, and the dormant source
`985f7109` keeps it. Not every odd own id is refused: a falsy request_id (false,
0, "", [] or a one-element falsy array) is never inventoried, refused or listed
in either mode. The reply index drops that row unless its in_reply_to_request_id
is truthy, and then the getter skips it as a null id or a reply. A one-element
array id such as `["x"]` reads as `"x"`. Both modes also fail closed on index
read errors and on a request-like row with no top-level agent and no string
payload agent.

The opt-in `-DiagnosticPartial` switch is unavailable in the installed baseline
`8a7576af`. It is present only in source, from checkpoint `9cde2f8f` (getter
`165453d7`) in the dormant integration `985f7109`. Instead of throwing on the
conflicting or malformed rows above, it lists them with their metadata and
excludes them, in a DIFFERENT schema, `wd.request-inventory-diagnostic.v1`, with
`complete=false` always and status `partial_unknown` or `no_conflict_observed`:
explicitly incomplete diagnostic evidence. The list is a prefix in indexed order
of at most 50 entries and 30000 JSON characters; later rows are only counted.

A count of 50, whether a full page or a full conflict list, is a bound, not full
coverage: follow `next_cursor`, and read `truncated` or `conflicts_truncated` as
incomplete. Neither mode gives a runtime, readiness or agent quota guarantee.
