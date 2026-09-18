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

The derived `shared/cache/reply-index.json` avoids reparsing old JSON rows. Each
query hashes the canonical prefix and checks the reader's file identity/generation
before accepting the cache, then parses new complete rows. The cache has a content
checksum, complete-prefix marker and row count, and is atomically replaced under an
exclusive lock. Missing, truncated, corrupt or rotated caches rebuild from the log;
an incomplete/invalid canonical read throws instead of returning `pending`.
`-NoCache` performs a full reference read. Cache failure never changes the canonical
log, delivery cursor, ACKs or request authority. Cache checksums detect corruption,
not a hostile local writer with permission to rewrite both cache and checksum.

Fleet health compares resources from unexpired claim files using the same scope
resolver as claim acquisition. A checkpoint's historic scope is not an active claim.
Malformed claim evidence is explicitly `unknown`. Recent canonical answers and
checkpoint age are displayed separately; answer observation alone does not verify
binding, correctness or task completion. Native Lead relay status/error is exposed
even when readiness is blocked.

Latency reports retain the compatibility name `model_turn_started`, but label it
as agent registration of request processing. Actual engine start remains unknown.
After inspecting a bound reply, Lead can call `Record-BridgeReplyObservation.ps1`
with the full request/reply JSON and `-Stage lead_processed`. After publishing the
summary it may record `user_reported` with an actual `-ReportReference`. Both stages
are agent-reported observations; neither proves the operator saw the report. Missing
stages stay unknown, and negative phase durations are not treated as valid timings.

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
An explicitly anchored direct lane resume also exports that verified manifest hash
to the native child, so pinned Python/Grok calls use the same bundle as the launcher.
