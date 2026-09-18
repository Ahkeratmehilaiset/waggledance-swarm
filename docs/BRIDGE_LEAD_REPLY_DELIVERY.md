# Native Lead replies and Grok visibility

The normal Codex Lead terminal retains its recorded conversation and model controls.
Its launcher holds the native process handle and relays watcher notifications with
`codex queue --thread <recorded-thread>`. No focus, custom window, extra model session,
or operator prompt is needed. Tools and Lead use the same queue implementation.

Queue acceptance is transport evidence, not proof that the model read or completed
a request. `native-terminal.json` records the Lead relay and native process lifetimes.
Fleet health and startup require those identities before reporting native queue support.
An ambiguous submission remains blocked for reconciliation; it is never blindly retried.

Before reporting an outgoing request's reply state, use the installed helper:

```powershell
& "$env:WD_BRIDGE_BIN\Get-BridgeReplySnapshot.ps1" -RequestId '<exact-request-id>'
```

The helper reads a complete canonical snapshot, validates immutable request content
and the existing requester/responder binding, and returns full matching answers.
ACKs and wrong-session answers leave the request pending. Missing requests, corrupt
history and partial records fail explicitly; they are not evidence that a peer is silent.
`pending_at_snapshot` applies only to the recorded snapshot. A later answer wakes the
same Lead conversation and the continuation instructions require a correction or
supplement to an earlier pending report. There is no atomic transaction between a
model's final text and concurrent peer replies; report the observation time honestly.

The hourly Grok helper emits advisory lifecycle events: started, answered, failed,
or deferred. The viewer labels them `GROK`. Results include the report path and hash;
private prompts and full response text are not copied into the shared log. These
events grant no approval authority and do not turn Grok into an independent worker.
Bridge emission failures are recorded in hourly state without refunding the budget
or repeating the model call. Read-only `-Status` never emits or invokes the model.

Deployment requires the existing exact-head tests and review gates. Resume Lead in
its existing native thread to adopt the relay; restart the read-only viewer to adopt
the Grok label. A live legacy Lead without the relay must not be silently accepted
as continuously bridge-connected merely because its terminal is open.
