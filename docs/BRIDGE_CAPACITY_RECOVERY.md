# Capacity observation and guarded recovery

The capacity tools have three separate boundaries:

1. `bridge_capacity_advisor.py` proposes decisions from an explicitly supplied
   policy and snapshot. It never grants execution authority.
2. `bridge_capacity_collector.py` reads Codex subscription metadata through a
   private stdio App Server. It does not start or resume a thread, generate tokens,
   change login, consume reset credits or change a model. Claude statusline JSON
   can be ingested on stdin. Missing or stale information remains unknown.
3. `bridge_capacity_recovery.py` implements a durable transition journal and
   conservative shared-quota admission for a **trusted owning-session adapter**.
   No production adapter or command to control live peer terminals is supplied.

## Observe without changing models

Use an absolute native Codex executable, not a shell wrapper. The SQLite parent
directory must already exist and be writable only by the operator/service user.

```powershell
python tools/bridge_capacity_collector.py --provider codex --codex-executable C:/path/to/codex.exe --store C:/path/to/observations.sqlite --scheduled
python tools/bridge_capacity_collector.py --store C:/path/to/observations.sqlite --status
```

`--scheduled` reserves one attempt per five minutes atomically across callers;
failed attempts count. The whole metadata call has a 45-second deadline, and only
the private child process can be stopped. A backwards clock suppresses new polls.
No model retry layer is added. Observation history is bounded to 2048 records.
Status reads the database in read-only mode. A newer collection failure invalidates
older positive observations. An old successful row is never silently called current.

Windows installation uses `ops/windows/reboot/Install-WdCapacityObserver.ps1`.
The default prints a plan; `-Apply` copies a clean committed version into a
commit-addressed directory and registers `WD-CapacityObserver` with a Limited
interactive user principal, logon trigger and one-minute admission checks. The
collector still admits at most one provider attempt per five minutes; intervening
checks exit without starting an App Server. This avoids a small scheduling jitter
turning a five-minute budget into a ten-minute collection gap. Its hidden
runner verifies the manifest, source and executable hashes on every invocation.
It does not modify WD-Supervisor or restart agent conversations. An executable
update that changes the pin stops collection visibly in Task Scheduler; revalidate
and run the installer with `-Apply -Update` before resuming it. That path verifies
the existing task against its previous manifest, requires the previous invocation
to finish, backs up its task definition and replaces only the exact owned Limited
task. Release directories bind the source commit and executable hashes, so a CLI
upgrade does not overwrite an existing manifest. Interrupted registration is
retryable against the same exact package. A stale observation
does not remain usable while the observer is stopped.

The Codex account API currently exposes an account type and plan/email, but not
an authoritative quota-pool-to-lane mapping. The collector stores only a digest of
the auth context/account shape; it does not store emails or credentials. An auth
context is **not** a verified account pool. Profiles must not become eligible for
automatic switching until operator-owned mapping evidence binds the actual lanes,
provider, account pool, limit IDs and every applicable window. Two model names
with the same quota do not provide independent capacity. Session token counts
remain separate from subscription quota percentages.

Claude ingestion uses the documented `session_id`, `model.id`, `effort.level`,
`rate_limits`, and token counters. It does not read transcripts or credentials.
Statusline quota fields may be absent until an API response; ingestion cannot
refresh an idle subscription without provider support. Never fabricate freshness
by polling the same cached statusline payload and replacing its observation time.
The collector explicitly labels the timestamp as a callback observation and marks
provider quota freshness unknown, even if a new UI update supplied the callback.
`--provider claude --statusline --store <path>` saves the input and renders a short
plain status row. It can be configured in lane-local Claude settings without
replacing a pre-existing statusline or changing global/outside sessions. Settings
integration must preserve and back up the exact previous settings bytes.

## Recovery semantics

`recovery_advice` separates a bounded retry on the same profile, waiting for quota,
operator-required errors and a fresh safe-boundary check. A newer matching quota
error invalidates prior headroom. For simultaneous exhausted windows, the latest
reset is the earliest possible recheck. A reset time is not permission to resume.
Spend/workspace limits, invalid times and missing observations do not grant use.

Transition intent binds the exact task/request revision, HEAD, claim, scope,
native conversation, permissions, policy and qualified model/effort profiles.
SQLite admission reserves all `(provider, verified account pool, limit ID, window)`
keys together. Only one transition may use an overlapping window. This is a
conservative transition-admission bound, **not** a token reservation or a fleet task
scheduler. Reservations remain held after ambiguous outcomes, without lease expiry
that could authorize another uncertain operation.
The trusted mapping layer must supply byte-identical canonical opaque account,
limit and window IDs for the same resource. Provider names must be exactly `codex`
or `claude`, and surrounding whitespace is rejected. Opaque provider IDs are not
case-folded: their issuer may assign significance to case. Friendly aliases must
be resolved by the verified mapping layer before admission, not used as pool keys.

The journal proceeds through `planned → quiesced → checkpointed → apply_pending
→ verified → resume_pending → resumed`. A lost acknowledgement leaves durable
intent. The next step observes the actual profile and operation ID; it never
repeats apply/resume blindly. Changes to HOLD, cancellation, HEAD, scope, reviewer
requirements, quota, identity or permission policy stop progress. Inspection must
be fresh and must verify actual model and effort. Unknown stays blocked.

Adapter requirements: own the exact session; enforce guards atomically at side
effects; make checkpoint creation idempotent by transition ID; keep profile apply
separate from task execution; expose durable applied/resumed transition IDs; preserve
the existing queue and all release gates. An in-process fake tests this contract.
It is not proof that an arbitrary terminal can support the contract. Production
observations and policy must be authenticated by the caller, never copied from a
peer's claimed approval fields. The library does not verify qualification artifacts.

## Activation gates still required

- Verified real lane/kiintiö mapping and per-role model+effort quality evidence.
- Owning-session adapter with actual model/identity acknowledgement and safe-point
  guarantees; no keystroke injection or automatic restart of another terminal.
- Independent review and exact-head CI, then a bounded low-risk canary.
- At least one real reset cycle and a separate 24-hour observation period.

Until these are proven, production is observation-only. Lead retains its strong
profile, RCO requirements are unchanged, Grok retains its existing hourly helper,
and no cross-account/provider or paid API fallback is allowed. All approved quota
being exhausted means durable waiting, not a promise of continuous inference.

Protocol references checked 2026-09-19:
[OpenAI App Server](https://learn.chatgpt.com/docs/app-server) and
[Claude statusline](https://code.claude.com/docs/en/statusline).
Allowed Codex limit enums are pinned from codex-cli 0.154.0's generated
`v2/GetAccountRateLimitsResponse.json`; new unknown enum values remain unknown.
