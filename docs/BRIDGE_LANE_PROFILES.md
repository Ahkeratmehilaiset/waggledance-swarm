# Bridge lane profiles

Status: **PR-1 of lane profile switching: catalog and validator only.** Nothing in
this PR launches, stops or reconfigures a lane, and nothing reads or writes runtime
state. The feature is specified on bridge task `lane-profile-switching`, spec v3.

## Terms

A **profile** is the capacity layer's profile: provider, account pool, model and
effort, plus that profile's quota buckets. The term is reused from
`tools/bridge_capacity_advisor.py`; there are no "weights".

A **lane** is one bridge agent: codex-lead-1, codex-tools-1, claude-rco-1,
claude-rco-2 or fable-5. grok-scout-1 is deferred, because it has no launcher path.

## The catalog

`configs/lane_profile_catalog.json`, schema `wd.lane-profile-catalog.v1`, is a
versioned wrapper. Its validator, `tools/lane_profile_catalog.py`, covers every field:

- `capacity_policy`: the advisor's `wd.bridge-capacity-policy.v1` object, carried
  whole and validated by the advisor's own `_validate_policy`. The wrapper also
  refuses any key the advisor does not read, at the root, per profile and per agent
  binding, because the advisor does not reject unknown keys. Nothing may ride inside
  the policy that one consumer honours and another ignores. The advisor and the
  planner consume this same validated object.
- `providers`: the effort enum per provider. Every profile's effort must be in it.
- `lanes`: per lane, `allowed_profiles` is ordered **strongest -> weakest**. That
  order is the only definition of *raise* and *lower*. It must be an in-order subset
  of the lane's advisor allowlist, so it can narrow the list but never widen or
  reorder it. The other fields are `floor` (the lowest index Lead may choose without
  an operator ack), `default` (never below the floor), `max_relaunches_per_hour`,
  `cooldown_seconds` and `reviewer`, which must be true exactly for claude-rco-1 and
  claude-rco-2.
- `fleet`: `mode` (`shadow` | `approve` | `auto`), `max_relaunches_per_hour_total`
  (no lane budget may exceed it), `verify_timeout_seconds`, and the `shadow_exit` and
  `approve_exit` criteria.
- Exact keys everywhere: the root, each provider (exactly `efforts`), the policy
  root, each profile, each quota limit (exactly `id` and `windows`) and each agent
  binding. An unknown key anywhere is refused.
- Runtime admissibility: every profile a lane lists must pass what the advisor's
  `_profile_checks` checks at runtime. It needs qualification classes, the lane's
  role and subscription billing. A lane may not mix providers or account pools,
  because a resume keeps the conversation but cannot move it.
- Signature state decides approval, for every profile any advisor agent binding can
  reach, not only the lane-listed ones. A catalog whose `operator_signature` starts
  with `UNSIGNED` (ignoring case and leading whitespace) must mark every profile
  `approved: false`, so the advisor finds no admissible candidate and no shadow
  decision counts an unqualified profile. A signed catalog must mark every reachable
  profile `approved: true` with a real `qualification_ref`.
  - The placeholder refusal is a **heuristic**. It rejects references containing
    REQUIRED, PLACEHOLDER, SYNTHETIC, TODO or UNSIGNED, and would accept others such
    as TBD. It is a tripwire, not proof that a qualification exists.
  - "Signed" is a **label**. The validator reads the signature text but cannot verify
    that the operator wrote it. The signature is operator-attested by an
    operator-reviewed PR that records the catalog's sha256. It fails safe, because a
    signed catalog must then carry real approvals on every reachable profile.
- Exit criteria may be stricter than the operator spec, never weaker. They must
  require at least 20 shadow decisions over 5 days with 0 marked wrong, and at least
  10 approve transitions with 1 induced rollback and 0 wrong-process kills.
- Loader guards, the same as the advisor loader's: a bounded read (at most 256 KiB
  plus 1 byte is ever read), no duplicate JSON keys, no non-finite numbers, no
  RecursionError, and no symlink or reparse-point catalog path.
- `catalog_ref`, `operator_signature`: the operator signs by replacing
  `operator_signature` in a reviewed PR. The validator records the signature; it does
  not verify it.

`load_catalog(path)` reads exactly that one file, bounded to 256 KiB, UTF-8 JSON
with no NaN or Infinity. It returns the catalog and the sha256 of its bytes. Every
later receipt carries that hash.

## Effective mode

`effective_mode(catalog)` returns the weaker of `fleet.mode` and
`capacity_policy.mode`. It is the only mode a consumer may act on, so `fleet.mode` is
not a second switch. The advisor accepts only a shadow policy, so today the effective
mode is `shadow` whatever `fleet.mode` says.

## Transition classification

`classify_transition(catalog, lane, current, target)` is pure. Its verdicts:

| verdict | when |
|---|---|
| `same` | target == current |
| `raise` | target is stronger than current and within the floor |
| `lower` | target is weaker, still within the floor, and the lane is not a reviewer |
| `park` + `operator_ack_required` | target not allowed; target below floor; current unknown or not in the catalog; reviewer lowering; lane not in catalog |

A reviewer lane never lowers without an operator ack, even inside its floor, because
a reviewed party must not weaken its reviewer. An unknown current profile always
parks, since a raise from an unknown state cannot be proven to be a raise.

## What is measured

Only the shipped defaults were measured on 2026-09-26. Lead's and Tools' profiles
were confirmed by `read_native_codex`, and the Claude lanes by their statusline. The
stronger options (gpt-6-sol, claude-opus-5-5-xhigh) are catalog choices, not
measurements. The Claude effort enum matches the installed CLI's `--effort` help. The
Codex enum, and `max` in particular, is not verifiable from codex-cli 0.157.1.

## Shipped defaults

The defaults are the profiles measured on 2026-09-26: rco-1 and rco-2 on
claude-sonnet-5 xhigh, Lead on gpt-5.6-sol medium, Tools on gpt-5.6-terra medium,
and fable-5 on claude-opus-5-5 medium. Each lane gets one stronger option. Each
lane's floor is its current profile, so the shipped catalog permits raise-or-same
only. The mode is `shadow`, the signature reads `UNSIGNED-DEFAULT`, and every
profile is `approved: false` until the operator signs.

## Lane runtime record (D2, PR-2)

`tools/lane_profile_record.py`. The record lives at
`<runtime_root>/lane_profiles/<lane>.json`; `record_path` refuses anything but a known
lane, so there is no traversal. It is runtime state, and `wd-fleet.json` keeps
`"model": "native"`. Schema `wd.lane-profile-record.v1`, with exact keys:

- **Transition fields:** `lane`, `desired_profile`, `previous_profile`, `reason`,
  `request_id` and `transition_id`.
- **`requested_by`:** a lane plus its `agent_uuid` and `session_id`. The label
  `operator` is not an identity and is refused.
- **Lifetime:** `created_at` and `expires_at`, which must be aware timestamps. The
  lifetime is positive and at most 24 h, and a record created in the future is refused.
- **`catalog_sha256`:** must equal the loaded catalog's hash.
- **`launched`:** `null`, or exactly `native_thread_id`, `pid`, `process_started_at`,
  `session_id`, `run_id` and `launched_at`.

A record whose previous-to-desired transition would `park` is refused. A reviewer
lowering or a below-floor target needs an operator decision, never a record. Writes
are atomic (temp file, fsync, rename). Reads are bounded to 64 KiB and reject NaN.

`launch_decision(runtime_root, lane, catalog, digest)` is the launcher's question:
- **No record:** `native`, silently.
- **Unusable record** (expired, wrong hash, park, another lane): `native` plus a
  `fallback_event`.
- **`shadow`:** always `native`, and `would_apply` names the profile.
- **`approve`:** fails closed with `operator_ack_unverifiable` until an operator ack
  can be verified.
- **`auto`:** `apply` with the exact provider, model, effort and transition id.

`launch_decision` never raises. A hostile record (nesting bomb, oversized integer,
out-of-range offset, a directory or locked file, duplicate keys) gives `native` with
`record_unusable`. `auto` also re-validates the catalog and requires it to be signed,
so apply never rides on a caller-built dict. `read_record` refuses symlinks and
reparse points, and never reads more than 64 KiB + 1 byte.

A record is a temporary override. When it expires (at most 24 h), the next launch is
`native` again, and the profile reverts. That is intended. A lasting change belongs
in the catalog default, by an operator-signed PR. `write_record` does not validate:
its callers (D4) validate before writing, and every read validates again.

Nothing in PR-2 calls this. The launcher wiring is PR-4, which is (a)-class.

## Session binding (D3, PR-2)

`tools/lane_profile_binding.py`'s `bind_lane(...)` reports two independent states
and never merges them:

- **`session_identity`** (`valid` | `unbound` | `invalid`). The recorded pid must be
  live with the recorded creation time, within 2 s, or it is `invalid` (dead, or pid
  reused).
  - Claude: the capacity observation's `native_thread_id` must equal the recorded
    thread, observed at or after `launched_at`.
  - Codex: the `read_native_codex` result must name the recorded thread, with a turn
    strictly after `launched_at`.
  - The freshness boundary is `launched_at`, the moment the launcher recorded the
    target profile, not the process start. A process can exist before its profile is
    applied. The causal order is `created_at <= process_started_at <= launched_at <=
    now`: the relaunched process is created after the transition record. A record
    violating it is refused, and binding re-checks the order, so an older epoch never
    binds a newer transition.
  - Missing evidence is `unbound`, never valid.
- **`quota_pool_binding`** (`valid` | `unverified` | `invalid`). Every one of the
  profile's `(provider, limit id)` quota rows must be present, all with the profile's
  account pool. A thread binding never authenticates a quota row, and a quota row
  never authenticates a session.

Input contract: `live_processes` maps pid to the process creation time, and every
timestamp is an aware ISO-8601 string. A CIM DateTime must be converted first; an
unconverted value fails closed as `process_epoch_unparseable`. An unobserved quota
account pool (the collector reports `None` today) is `unverified`, not `invalid`.

`profile_observed` (`match` | `mismatch` | `unverified`) compares model and effort.
The Claude context suffix (`[1m]`) is stripped for the comparison, and the raw value
is reported.

## Relaunch checks (D4 steps 1-2, PR-3a)

`tools/wd_lane_relaunch.py` is pure. Every verdict carries
`execution_allowed: false`: passing a check is not authority.

- `check_request(catalog, request, history)`:
  - A catalog park (target not allowed, below floor, unknown current, reviewer
    lowering, unknown lane) parks with `operator_ack_required`. The same profile aborts.
  - Otherwise it checks the per-lane hourly budget, the fleet hourly total and the
    lane cooldown against prior receipts.
  - Unparseable or future-dated history parks; it is never read as "no history".
  - A request whose lane or target is not a string, or whose current profile is
    neither a string nor absent, parks as `request_malformed` and never raises.
- `check_safe_boundary(state, lane=...)`: the measurement must name `lane` itself
  (otherwise `lane_state_names_another_lane`). The lane must be idle, with
  `pending_effects` false, no previous-turn blocker, no open claims, and a fresh
  measurement at most 60 s old with a known current session. Unknown values and
  hostile types block and never raise.
  - Every unresolved request bound to the **current** session blocks regardless of
    age (Lead LPS-B2).
  - A request bound to another session blocks unless the lane's recorded
    `session_lineage` (session -> successor rows written by the launcher) chains from
    that session to the current one. A `superseded` flag on the request is ignored.
    Malformed, forked, cyclic or over-long lineage (more than 64 steps) is unknown
    and blocks.
  - The supervisor is never a target.

## Planner (D5, PR-3a)

`tools/wd_lane_profile_planner.py`'s `plan_lane(...)` returns one decision,
`wd.lane-profile-plan.v1`, and never acts:

- The current profile comes only from a `valid` session binding for the same lane,
  mapped to an allowed profile. A binding that names another lane (or none) parks as
  `binding_names_another_lane`, so it can never count as a shadow decision for this
  lane. The Claude context suffix is stripped. Anything else parks.
- Admission `KEEP` with an available bucket keeps. `PARK` or unknown parks.
- An exhausted or limited bucket, or `ESCALATE`, looks for another allowed profile,
  strongest first. It stays within the floor, and reviewers only raise. `ESCALATE`
  only raises.
- A candidate must have an `available` bucket (unknown counts as not available) and
  must pass `check_request`. All Claude profiles share the `claude` bucket, so
  exhaustion there has no Claude escape, and the planner says so by parking.
- In `shadow` the strongest result is `would_relaunch`.

## Relaunch executor (D4 steps 3-9, PR-3b)

`tools/wd_lane_relaunch_executor.py`'s `Executor(...).run()` drives one transition
through an injected `Ports` object. There is no production implementation and
nothing calls it; PR-4 wires it, and that is (a)-class.

**Journal.** It uses the existing `RecoveryStore`, not a second journal. A relaunch
replaces the process, so it does not use the same-process `advance()` driver.
Instead it journals both epochs:
- the source epoch is the measured pid, creation time, session and thread, measured
  before the stop;
- the target epoch is bound once, only from the launcher-written D2 `launched` facts,
  and only when execution evidence (pin `manifest_and_launcher_verified`, pid,
  creation time, native conversation id) agrees and a post-launch observation shows
  the target model and effort;
- a caller-supplied pid is never accepted.

**Ports.** Every side effect goes through the port, and three ports carry authority:
- `authenticate(request)` authenticates both the process running the executor and
  the origin of the request. Nothing is taken from constructor or request labels.
- `processes(lane)` enumerates every live process attributed to the lane, including
  a late target, each with pid, creation time, pin status and native conversation
  id.
- `resume_lane(lane, epoch, checkpoint)` returns exactly `True` only when the bound
  session confirms that it received the checkpoint or resumed its provider thread.

**Order.** Fail closed at every step:
1. Request shape (lane, request_id, requested_by identity, both profiles).
2. Authentication (Lead review R7):
   - an unauthenticated or malformed answer parks with `principal_unauthenticated`;
   - the claimed requester must equal the authenticated one, and only a Lead
     request is executed;
   - for another lane, the executor must be that same Lead session;
   - Lead's own lane runs only through the `supervisor` principal;
   - the journal's `trusted_adapter_identity` carries the port's
     `verification_ref`.
3. `check_request` against the durable journal (Lead review R6):
   - a lane-profile transition counts, fleet-wide, from its `apply_pending` row.
     That row is written with a `source_stopped_at` marker in the same SQLite
     transaction as the stop;
   - aborted, parked and cancelled attempts never count;
   - emitted receipts and the request's `history` field are never read;
   - an unreadable journal, or a stopped transition without its marker, parks with
     `relaunch_history_unknown`.
4. Mode gate: shadow gives `would_relaunch` and touches nothing. approve fails closed.
   Only exactly `auto` proceeds, and it needs a signed catalog.
5. The measured state must name the request's lane, on both measurements. Then:
   - `check_safe_boundary`;
   - the current profile is verified from the measured process and an observation
     at most 300 s old. The request's or record's claim is never trusted (rco-2
     residual B);
   - execution evidence must show exactly one lane process: the measured source,
     pin-verified.
6. Claim the record, the transition lock and the readiness path with a lease of at
   least 2 x verify_timeout + 600 s. Re-measure, re-prove the source from evidence,
   and abort if anything changed.
7. The new process's launch preconditions are checked before the old one stops.
8. Continuity: provider resume, or a fresh checkpoint.
9. Journal planned -> quiesced (record written) -> checkpointed.
10. Stop only the verified source instance, then apply_pending (with the stop marker)
    and launch. If `stop()` raises, the source's fate is reconciled: only
    pin-verified evidence of that very process proves it alive, which cancels.
    Anything else is an unknown fate and holds the reservation for the operator.
11. Verify within `verify_timeout_seconds`, counted from when the launch returns,
    with a creation-time skew of 2 s between sources. The lane must then have
    exactly one process. A launch that raises is treated as a target that never
    bound. Otherwise decide the stray from the enumerated processes:
    - no live process: nothing is stopped;
    - exactly one evidence-verified process that the launcher record corroborates
      (pid and creation time): that process is stopped;
    - any other case, including a second (late) process, a pid named only by the
      record or an unverified pin: `stray_identity_unproven`, nothing is stopped
      and an operator reconciles.

    Then make exactly one rollback. The rollback record is previous -> previous, a
    restore and never a lowering.
12. Resume: after verification the journal moves to `resume_pending`, and
    `resume_lane` delivers the checkpoint (or `provider_resume`) to the bound
    session. Only an exact `True` reaches `resumed`, and the same holds after a
    rollback.
13. Receipt, then release the claim.

**Outcomes** (`decision/profile_transition`, schema `wd.lane-profile-transition-receipt.v1`):
- `applied` and `rolled_back` end the journal at `resumed`, which frees the quota
  reservation.
- An unconfirmed resume gives `failed` with `resume_not_confirmed` and
  `operator_required`, and holds the journal at `resume_pending`.
- A failed source stop ends at `cancelled_before_apply`, and the record is rewritten
  previous -> previous so the next launch cannot apply the aborted target.
- An unexpected exception still yields a `failed` receipt (`executor_exception`).
  Before the source stop it cancels the reservation and neutralises the record.
  After it, or when the stop's outcome is unknown, the reservation stays held
  (`operator_required`). A failing emit or claim release is recorded in the
  returned reasons.
- A failed rollback leaves the lane stopped, the journal at `apply_pending` and the
  reservation held. Only an operator reconciles it (`operator_required`).
- A second transition on a reserved quota bucket is refused by the journal (`parked`).

**Record I/O** (rco-2 residual A) refuses a record path whose file or any existing
ancestor is a symlink or junction. A junctioned `lane_profiles` directory cannot
redirect reads or writes outside the runtime root.

## Governance

The catalog, its floors and any change to `fleet.mode` are (a)-class, needing an
explicit operator signature and never standing consensus. So is every later PR that
wires this onto a runtime path. `approve` stays unavailable until
configs/bridge_identity_registry.json binds an operator identity. On a single-user
host the agents run as the same Windows user, so that binding is policy plus audit,
not cryptographic isolation.

## Non-goals

No token reservation, no fleet task scheduler, no account or quota-pool changes, no
cross-session context sharing, no change to merge-gate authority, and no attaching
to or steering a peer session's turns.

## What this PR does not prove

The validator proves that a catalog is well formed and internally consistent. It does
not prove the following:
- that a profile's model is available, or that its quota has headroom;
- that the operator actually signed;
- that any relaunch would succeed.

Those need the later deliverables: the D2 record, D3 binding, D4 verified relaunch
and D5 planner, and live evidence in `approve` mode.
