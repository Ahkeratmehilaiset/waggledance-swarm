# Owning-session descriptor boundary

`tools/bridge_owning_session.py` validates a small, supplied descriptor for a
future Codex owning-session adapter.  It is dormant and pure: it does not read
files, inspect processes, contact an endpoint, start or stop Codex, enqueue a
turn, select a model, or change any setting.

## Two inputs, two trust levels

The descriptor is **untrusted observation data**.  It describes a specific
agent, UUID, run/session, thread, native and launcher PID/start epochs,
generation, CLI hash, and a syntactically valid `ws`/`wss` endpoint.  It must
identify the surface as `app_server_owned`, the readiness scope as
`owner_bound_observation`, and the adapter kind as `owning_session_adapter`.

The second input is independent, externally supplied launcher evidence.  It
must contain strict boolean `owner_verified: true`, the canonical SHA-256 of
the exact descriptor, and an exact copy of every binding and transport field.
The caller, not this library, is responsible for obtaining that evidence from a
verified owning launcher.

`owner_verified` is forbidden inside the descriptor itself.  A descriptor can
never prove its own ownership.

## Fail-closed cases

The validator rejects, among other cases:

- `native_terminal` and `native_cli_only` records;
- `capacity_collector` records and PID-only identities;
- absent or malformed endpoints, including malformed bracketed hosts and
  invalid ports (these are rejected rather than allowed to raise);
- booleans masquerading as PIDs;
- mismatched agent/run/thread/process-start/generation/CLI bindings;
- stale, too-long-lived, future, or caller-reported replayed descriptors; and
- forged self-attestation or non-boolean launcher verification.

The replay set and the observation timestamp are explicit caller inputs. UUIDs
in both the descriptor and replay guard must use canonical lowercase form. The
guard consumes at most 4,097 untrusted iterable items before rejecting an
oversize input, so it never materializes an arbitrary or infinite iterable.
The module stores no replay state and does not obtain time or process
information itself.

## Result boundary

A valid result means only that supplied observation data is exactly bound to
the supplied trusted launcher evidence for its bounded lifetime.  It sets
`observation_allowed=true`, but always sets `control_allowed=false`,
`live_capability="none"`, `model_calls=0`, and `io_operations=0`.

It cannot prove a live endpoint is reachable, that it controls a session, or
that a turn/model/catalog operation is safe.  A functional adapter still needs
an explicitly owner-created App Server transport at controlled cold start,
independent review, and activation gates.  It must never attach to a running
`codex resume` terminal solely from PID, parent, queue, or thread data.
