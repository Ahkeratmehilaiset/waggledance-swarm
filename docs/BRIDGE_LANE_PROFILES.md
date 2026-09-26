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

`profile_observed` (`match` | `mismatch` | `unverified`) compares model and effort.
The Claude context suffix (`[1m]`) is stripped for the comparison, and the raw value
is reported.

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
