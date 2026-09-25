# Owned app-server observation transport

`tools/bridge_owned_codex_transport.py` makes a bounded, read-only observation
of an app-server child **that this process spawned itself**.

It exists because a descriptor validator can only judge a claim somebody else
made. `tools/bridge_owning_session.py` does that job well and deliberately never
touches a process. This module is the other half: it performs the observation,
and the only reason it can do so honestly is that it owns the child.

**Nothing here is wired to anything.** No launcher calls it, no scheduled task
runs it, and it is not in any packaging manifest. It is a proposal.

## What the protocol actually permits

From the official app-server documentation:

* A client **spawns `codex app-server` as its own child** and speaks
  newline-delimited JSON (one JSON object per line) over that child's stdio.
* Messages use JSON-RPC 2.0 *semantics without* the `"jsonrpc"` field:
  request `{method, id, params}`, response `{id, result}` or `{id, error}`,
  notification `{method, params}` with no `id`.
* The handshake is mandatory: `initialize` (with `clientInfo`) → result carrying
  `userAgent`, `platformFamily`, `platformOs` → `initialized` notification.
  **No other method is allowed before it completes**, and a second `initialize`
  returns an "Already initialized" error.
* `model/list` is a read-only discovery method taking `limit` and
  `includeHidden`.

### The limit that shapes everything else

> There is **no mechanism to attach to, or observe, an app-server instance
> launched by another client.** A client must spawn its own process.

Read that precisely, because an earlier draft of this page overstated it. It is
a statement about the **documented API**: the protocol exposes no attach method.
It is *not* a security boundary, and it does not say that a running process is
unobservable by other means — a debugger, an OS facility, or a future protocol
version are all outside what that sentence covers.

What follows for us is narrower but still decisive: **through this protocol**,
owned observation describes the child we started and offers no way to describe
a peer lane's live session. A design that promises otherwise is promising
something the documented API does not provide. That is the thing to carry into
a stage-5 discussion, and it should be carried as an API limit rather than as a
guarantee about processes in general.

## Why the surface is three methods

`ALLOWED_METHODS` is exactly `initialize`, `initialized`, `model/list`.

`turn/start` is the only path in the protocol that makes a model execute, and it
is not in the list.

Stated at the width the evidence supports: **this code path never sends
`turn/start`, so it cannot itself cause a model call.** That is mechanical, and
the allow-list test pins it. It is not a claim that the child never contacts a
provider for its own reasons — a freshly started app-server may do whatever it
does at startup, and this transport neither controls nor observes that. The
acceptance step that checks provider-side usage across the observation window
exists precisely because only measurement can close that gap. `thread/start`, `thread/resume`,
`thread/fork`, `turn/steer` and `turn/interrupt` are excluded for the same
reason: they change state rather than observe it.

The allow-list is enforced at the **single send site**, against the literal
method argument. A method name arriving inside `params` is data, never a method,
so it cannot reach the wire.

## Ownership, and what it refuses by construction

| Property | How it is enforced |
| --- | --- |
| Only a freshly spawned child | The constructor takes a `spawn` **callable**. There is no pid parameter, so there is nothing to attach to. A non-callable is refused with "a pid is never accepted". |
| Exclusive stdio | The transport owns the returned child's stdin and stdout for the lifetime of the context and hands them to nobody. |
| Never another agent's process or thread | The only child that exists is the one `spawn` returned during `__enter__`. |
| Pinned executable | `verify_executable` hashes the file on disk and refuses a mismatch **before** any spawn. It checks bytes, not a version string the process could claim about itself. |
| No shell | `default_spawn` passes an argv **list** to `subprocess.Popen` with `shell=False` and `close_fds=True`. No string is ever handed to a shell. |
| No dependency | Standard library only; a test asserts the import set. |
| No credentials in logs | Nothing from the environment is read or recorded. The only retained content is the bounded summary below. |

## Bounds

| Bound | Value | Why |
| --- | --- | --- |
| `MAX_LINE_BYTES` | 1 MiB | One frame. A longer line is a protocol fault, refused rather than buffered. |
| `MAX_TOTAL_BYTES` | 8 MiB | A chatty or hostile child cannot grow the session without limit. |
| `MAX_MODELS_RECORDED` | 64 | The observation is a summary, never the raw payload. |
| `DEFAULT_DEADLINE_SECONDS` | 20 | Every request has a deadline. |
| `MAX_DEADLINE_SECONDS` | 120 | A caller cannot disable the deadline by passing a huge one. |

The deadline covers **both halves**. An earlier version applied it only to
reads, so a child that stopped draining its stdin could block a write
indefinitely while the deadline looked enforced. A blocking pipe write cannot be
cancelled portably, so rather than pretend: each write runs on one short-lived
daemon thread joined for the remaining time only. If it has not finished we stop
waiting, mark the transport unusable so a half-written frame can never be
followed by another, and let `close()` terminate the child — which is what
actually releases the write. The residual is honest: the write may still be in
flight when we abandon it, bounded by the child's death rather than by
cancellation.

Deadlines are enforced by a reader thread feeding a queue, because a blocking
`readline` cannot portably be given a timeout, and a transport whose deadline
works on one operating system is a transport that hangs on the other.

Retained fields are exactly `user_agent`, `platform_family`, `platform_os`, and
per model `id`, `display_name`, `is_default`, `hidden` and
`default_reasoning_effort`, each string truncated to 256 characters.

The `model/list` envelope is the documented one: `result.data` plus
`nextCursor`. An earlier version read a `models` key the protocol does not
define, and its own fixture used the same invented key, so the code and the test
agreed with each other while both disagreed with the server.

### What the observation reports about itself

* `methods_sent` is **measured from the wire**, in order, not copied from the
  allow-list. The allow-list states an intention; only the record of what was
  written is evidence, and on a partial failure the two differ.
* `executable_verified` and `executable_digest` say whether the CLI was pinned.
  `verify_executable` used to sit beside the entry point without being wired to
  it, so an observation could look pinned while nothing had been checked. Pass
  `executable` and `expected_sha256` and the file is hashed before the spawn;
  omit them and the result says so, rather than staying silent.
* `cleanup_clean` and `cleanup_errors` report the teardown. `close()` used to
  swallow a failed `terminate`, `kill` or `wait` and still read as success; now
  a cleanup failure is recorded, and raised on context exit unless the body
  already raised something more informative.

## Failure handling

Every failure is one of four named errors, all deriving from `TransportError`:

* `TransportRefused` — a policy or input refusal. **Nothing was sent.**
* `TransportProtocolError` — the child said something outside the protocol: a
  non-JSON frame, a non-object result, a mismatched response id, an error
  response, an oversized frame.
* `TransportTimeout` — a deadline expired.
* `TransportChildError` — the child exited, crashed, closed stdout, or was never
  started with usable stdio.

The child is terminated and reaped on context exit **including when the body
raised**, because the error path is the one that leaks a child.

## What the tests prove, and what they do not

`tests/tools/test_bridge_owned_codex_transport.py` runs the real framing,
validation, deadline and cleanup code against a fake child that speaks the
documented protocol. **No process is spawned, no CLI is executed, no provider or
model is reached.**

That proves protocol handling. It does **not** prove live control, and no result
from this suite should be cited as evidence that a real child behaves this way.

## Next live acceptance requirements

Before anything here is treated as working, a reviewer-run cold start must show,
in this order:

1. **Pin first.** `verify_executable` against the real `codex` binary, with the
   expected digest recorded in the acceptance note. A mismatch stops everything.
2. **Cold start.** A child spawned by `default_spawn` with `app-server`, from a
   process that had no app-server child before, with its pid recorded.
3. **Handshake.** `initialize` returns `userAgent`, `platformFamily`,
   `platformOs`, and `initialized` is sent as a notification with no `id`.
4. **Discovery.** `model/list` returns a models list, summarised to the bounded
   fields. The raw payload is not stored.
5. **Wire transcript.** The exact methods sent are `initialize`, `initialized`,
   `model/list` and nothing else, captured from the transcript rather than
   asserted from the allow-list.
6. **No model call.** Provider-side usage for the account is unchanged across
   the observation window. This is the check that distinguishes a discovery call
   from a turn.
7. **Clean exit.** The child is gone afterwards, verified by pid, with no
   orphan, and the same holds when the observation is interrupted mid-handshake.
8. **Second start is independent.** A second cold start produces a new pid and
   does not disturb any running lane, confirming that nothing attached to an
   existing process.

Until those hold, this module is a proposal with mock evidence.

## Explicitly not authorised by this work

No stage-5 switch, no deployment or launcher wiring, no packaging entry, no
turn, no thread resume, no model or account change, and no claim that owned
observation of our own child says anything about another lane's session.
