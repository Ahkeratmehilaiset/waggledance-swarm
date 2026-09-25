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

So "owned observation" is structurally bounded: it describes **the child we
started**, and it can never describe a peer lane's live session. Any design that
promises otherwise is promising something the protocol does not offer. This is
the single most important thing to carry into a stage-5 discussion.

## Why the surface is three methods

`ALLOWED_METHODS` is exactly `initialize`, `initialized`, `model/list`.

`turn/start` is the only path in the protocol that makes a model execute, and it
is not in the list. That is the mechanical reason this transport cannot spend a
token, not a promise that it will not. `thread/start`, `thread/resume`,
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

Deadlines are enforced by a reader thread feeding a queue, because a blocking
`readline` cannot portably be given a timeout, and a transport whose deadline
works on one operating system is a transport that hangs on the other.

Retained fields are exactly `user_agent`, `platform_family`, `platform_os`, and
per model `id` and `display_name`, each truncated to 256 characters.

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
