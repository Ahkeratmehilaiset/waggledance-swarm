# Durable RCO task handoff and handback

Status: **implemented offline custody controller; not deployed model switching**.
`tools/bridge_rco_handoff.py` and its tests introduce no dependencies, provider
calls, model changes, bridge posts, claim mutations, background jobs or merge
gate changes. Existing cross-provider **session** transfer restrictions remain
unchanged. This transfers a logical review dossier, never a Claude conversation,
credential, or permission to impersonate a Claude reviewer.

## What this slice does

One local SQLite database serializes commands with `BEGIN IMMEDIATE` and
`synchronous=FULL`. Every successful command atomically records current state,
the payload-bound idempotency key, and the complete command/state history.
Revision compare-and-swap rejects concurrent stale writers. Ownership epochs
reject late old-owner updates. Replaying an identical command returns **current
state** plus the original applied revision; it never replays a side effect or
returns old ownership as permission. Reusing an ID with different content fails.

The transition sequence is identical in both directions:

```text
Claude active -> releasing -> awaiting_accept -> Codex active
Codex active  -> releasing -> awaiting_accept -> Claude active
                    |               |
                    +---- HOLD -----+  (no automatic resume)
```

`begin` requires confirmed fresh source exhaustion for substitution, not silence,
an estimated reset time, or an HTTP error inferred to be quota exhaustion. The
target must match the operator-fixed qualified profile, with fresh available
capacity. A recovered primary needs a stable availability interval, checked at
both begin and accept. Cooldown prevents rapid round trips.

`release` requires fresh, exact-owner quiescence evidence (idle, no pending
effects) and a valid checkpoint. `accept` requires the exact target identity,
new assignment request ID, new epoch, exact checkpoint hash, and a fresh target
capacity observation made **after release**. Acceptance is explicit; merely
observing a reset or availability cannot resume the original agent.

The quota-exhausted owner may be unable to reply. `host_release` therefore
accepts **host-authenticated** quiescence/fencing evidence, confirmed exhaustion,
expired source lease and an existing durable current-owner checkpoint. Lease
expiry or silence alone is insufficient. It cannot invent an absent checkpoint,
stop a process, enforce fencing or claim that unrecorded work was completed.

`progress` appends evidence. `complete` closes the custody task when its remaining
checks list is empty; **it is not RCO_PASS**, including when a Codex reviewer
completed the work. There is no pointless handback of completed work. `hold`
and `cancel` fence the current owner from any phase; held work cannot resume
through a capacity event. No HOLD clearing operation is supplied.

## Immutable bindings and independence

Each task binds `task_id`, full `head` and `base` SHAs, `pr_ref`, original
`request_id` and `request_digest`, `claim_id`, `scope_digest`, required reviewer
slots, author UUID, author native thread and author provider. All slots for one
task must bind exactly the same review round. A changed head requires a new
explicitly authorized round and reconciliation of open findings, not editing the
old task or carrying approvals forward.

Actual actor identity is separate from the logical slot: agent, UUID, session,
native thread, profile ID, provider, model, effort and account pool. This checks
profile equality, not actual runtime identity discovery. The host must provide
verified observations, not requested model labels. `qualification_ref` points to
external qualification evidence; a nonempty reference does not prove competence.

There is one fixed substitute per slot in a policy. An author cannot serve as
reviewer. The two review slots cannot reuse a worker UUID, agent name or native
thread, including workers that already completed, handed back, or were cancelled.
Participant history is retained permanently; the old actor is never relabeled
as the new one. `same_provider_as_author` makes common-provider review visible;
different worker identities are **not proof of independent reasoning**.

Checkpoints contain exact task/actor/epoch binding plus completed checks,
remaining checks, findings, veto references and evidence references. Findings,
vetoes, evidence and completed checks are append-only. References should resolve
to immutable dossiers with file/line, command, output, environment, exact SHA and
CI run IDs. This module preserves references; it does not read or verify those
external artifacts or resolve vetoes. Existing HOLDs must be imported by the
trusted host before it submits work; this database does not discover them.

## API and CLI

The canonical synthetic policy and complete command examples are in
`tests/tools/test_bridge_rco_handoff.py` (`policy`, `actor`, `task`, `checkpoint`,
`begin_command`, `accept_command`, `host_release_command`). They use deliberately
non-live model names and `TEST-ONLY` evidence. They must not become a production
roster by copying them.

Policy schema is `wd.rco-handoff-policy.v1`, mode `advisory_only`. It fixes
`authority_ref`, freshness/stability/cooldown durations, actor profiles and slot
primary/substitute bindings. A changed policy digest refuses existing-task
updates and requires reconciliation. The library surface is:

```python
from tools.bridge_rco_handoff import HandoffStore

with HandoffStore(db_path, authenticated_policy) as store:
    result = store.execute(authenticated_command)  # raises HandoffError on refusal
    state = store.get(review_id)
```

For the standalone offline source CLI (not an installed bridge helper):

```powershell
Get-Content -Raw .codex-audit/rco-handoff-command.json |
  python -B tools/bridge_rco_handoff.py --db .codex-audit/rco-handoff.sqlite --policy .codex-audit/rco-handoff-policy.json --stdin
python -B tools/bridge_rco_handoff.py --db .codex-audit/rco-handoff.sqlite --policy .codex-audit/rco-handoff-policy.json --show review-1
```

The command schema is strict. Unknown operations/fields, bad bindings, duplicate
JSON keys, non-finite numbers and oversized JSON are refused. Operational refusals
exit 2 with structured JSON; argparse usage errors remain ordinary CLI errors.
`--show` requires an existing database; database initialization still runs schema
checks and is not advertised as a filesystem read-only operation. The library
raises SQLite/storage errors instead of silently authorizing work on failure.

All results have `execution_allowed=false`, `rco_approval_allowed=false`,
`release_allowed=false`, `authority_effect=none`. Here `release_allowed` refers
to release/deployment authority, not the local custody `release` operation.

## Trust boundary and remaining activation work

This is **not** an authentication service or distributed lock. Protect policy and
database paths with the owning host's ACLs, on persistent local storage. Do not
use a network filesystem or expose arbitrary peer JSON to this API. A forged
`execution_fenced=true` or evidence reference is not proof. The controller assumes
host authentication; it does not authenticate caller identities, signatures,
capacity receipts, the reviewer roster or physical process state.

Live activation still requires separately reviewed integration:

1. A host-owned quota collector/classifier, authenticated fixed reviewer roster,
   qualification evidence and explicit allowed provider/account policy. A reset
   time means when to check again, not proof of usable quota. Shared account pools
   require admission/backoff across lanes. Lowering reasoning effort is not
   evidence of a separate quota pool. Existing advisor audit findings must be
   resolved before trusting its normalized observations.
2. Owning-session adapters that persist a checkpoint before work, enforce epoch
   fences **at every effect**, verify real quiescence, start a separate qualified
   Codex review session, and bind exact bridge requests/replies. Do not reuse the
   Lead author thread. Durable outbox/reconciliation is needed around external
   posts; this module emits none. Crash after real release but before DB commit
   must remain fenced/unresolved until host reconciliation, never assumed active.
3. Exact-head dual independent reviews, CI, fault-injected adapter acceptance,
   then an authorized rollout. Current gates still recognize the existing RCO
   identities: Codex findings can help, but cannot satisfy their approval slots.
   Expanding formal substitute approval is a separate explicit policy/gate change,
   not a side effect of the handoff ledger. Preserve StandingOneShot and all HOLDs.

This slice does not promise 24/7 completion, cheaper models, or a working live
failover fleet. It provides the durable tested custody foundation for that work.

## Verification

```powershell
python -B tools/select_affected_tests.py --files tools/bridge_rco_handoff.py tests/tools/test_bridge_rco_handoff.py docs/BRIDGE_RCO_HANDOFF.md --json
python -B -m pytest -q tests/tools/test_bridge_rco_handoff.py -o cache_dir=.codex-audit/rco-provider-handoff-20260919/pytest-cache --basetemp=.codex-audit/rco-provider-handoff-20260919/pytest-temp
```

Tests cover round trips, host release, binding/identity tampering, immutable veto
history, HOLD/cancel at each phase, freshness/recovery, same-provider visibility,
multi-connection CAS, durable retries, SQLite write failure, actual child-process
exit before commit, restart at each phase and malformed CLI input. These are
synthetic local tests, not real quota-exhaustion or physical-reboot acceptance.
CI remains the authoritative full-suite gate before any merge.
