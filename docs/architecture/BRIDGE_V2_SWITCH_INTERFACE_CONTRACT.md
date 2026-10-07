# Bridge v2 switch interface contract (F15 / F16 / F17)

Status: draft contract, Stage 0. Nothing here is activated. Every Bridge v2
flag stays off until the F0 policy is signed and enables it; the shipped
`configs/bridge_v2_activation.json` has every feature off and no signature.
Source plan:
`docs/architecture/BRIDGE_V2_IMPLEMENTATION_MAP_20260928.md` at
`c099c211a6fd71c109b6349e9e6ccd8794def297`. Code facts are read at base
`8a7576af01e310add445266ed78753a3409f8f7e` and cited as `file:line` there.

## 1. Components and authority

| Component | Slice | Authority | May do | Must never do |
|---|---|---|---|---|
| Switch policy `tools/wd_switch_policy.py` | F15 | none (pure) | read injected evidence, return a decision record | read the clock, files, environment or network; write anything; call a port |
| Intent queue `<runtime>/bridge_v2/intents/` | F16 | data only | hold one `wd.switch-intent.v1` per request | grant permission by existing |
| Executor `tools/wd_lane_relaunch_executor.py` | existing, F17 mods | acts only through `Ports` | journal, claim, stop, launch, verify, resume | act when F0 says disabled, or on an intent that fails validation |
| Production ports `tools/wd_lane_relaunch_ports_windows.py` | F16 | side effects | the thirteen `Ports` methods (§4) | decide policy; swallow a failure as success |
| Runner `ops/windows/reboot/Invoke-WdSwitchExecutor.ps1` | F16 | serializes | run one executor at a time under the supervisor | run lanes itself, retry a failed intent |
| F0 `tools/bridge_v2_activation.py` | F0 | gate | `evaluate(feature, ...)` -> `Decision(feature, enabled, reason, policy_sha256, revocation_version)` over the signed policy, the revocation/freeze state and the caller's pins | be bypassed by an environment variable; default on |
| F0 -> F15 adapter `tools/bridge_v2_switch_evidence.py` | F15 | none (pure; refuse-only) | re-check the caller-loaded F0 inputs against the caller's pins with F0's own validators; return the signed F15 parameters | enable anything on its own; read files, clock or environment; take a trust pin from the config it checks; mint a signature |

Separation rule: the policy decides, the executor sequences, the ports act.
A decision record is advice until the executor re-checks F0, the revocation
state and the safe boundary itself at dispatch and before every side effect.
Nothing in this chain is a merge, deploy, signature or Stage-2 authority.

## 2. Intent schema `wd.switch-intent.v1`

Required fields (unknown fields refuse; a missing or wrong-typed field refuses):

| Field | Type | Meaning |
|---|---|---|
| `schema` | `"wd.switch-intent.v1"` | exact |
| `intent_id` | 32 lowercase hex | unique; also the executor `request_key` input |
| `lane` | fleet lane id | the lane to switch |
| `source_epoch` | `{pid:int, process_started_at:str}` | the measured source; must equal the executor's own measurement (same source, CIM against CIM) |
| `target_profile_id`, `previous_profile_id` | catalog profile ids | the executor resolves both from the signed catalog |
| `catalog_sha256` | 64 hex | the catalog the decision read |
| `decision` | object | the F15 decision record (§3), copied verbatim |
| `requested_by` | `"policy"` or `"operator"` | an operator request still passes every gate |
| `created_utc`, `expires_utc` | ISO-8601 UTC | an expired intent is a terminal cancel, never a late switch |
| `supersedes` | intent id or null | a newer intent for the same lane cancels the older one before apply only |

Unknown stays unknown: an unobserved quota pool, an unverified account binding
or a missing observation is carried as `"unknown"`, never as a default number.

## 3. Policy decision record (F15 output)

`decide(evidence) -> {"schema": "wd.switch-decision.v1", "verdict", "reasons", "inputs_digest", ...}`

- `verdict` is one of `stay`, `switch`, `park`, `operator_required`.
  `switch` is the only verdict that may produce an intent.
- `reasons` is a non-empty ordered list of stable codes; an unknown input
  yields `park` with the input named, never a guess.
- `inputs_digest` is the SHA-256 of the canonical injected evidence, so a
  reviewer can recompute the decision.
- Every input is injected (catalog, F0 inputs, pacing windows, planner
  output, transition class, clock value). The module imports no I/O, and the
  same evidence always gives the same record.
- `catalog_sha256` is emitted in the record. It must equal the canonical-JSON
  sha256 of the injected catalog (`catalog_digest_mismatch` otherwise), so
  the intent's `catalog_sha256` names the content the decision read.
- Freshness: the session binding, `active_reviews`, `competing_intents` and
  `relaunch_history` are observed blocks `{observed_utc, items|entries}`
  within `evidence_max_age_seconds`. A bare list is not an observation and
  parks, because "no reviews" cannot be told apart from "not observed".
- `revert` is derived from the observed `relaunch_history`, never from a
  label. The lane's single latest receipt must be a switch from the target
  profile to the current one (`outcome: "switched"`, `from_profile`,
  `to_profile`). If that is missing or ambiguous, the intent is classified by
  direction and the dwell applies. The receipt field names are a proposal for
  the F16/F17 receipt writer; until receipts carry them, no intent is a
  revert.
- Contest: a strictly higher-precedence intent (the operator first) always
  keeps the incumbent. A conserve intent overrides equal precedence only.

### 3.1 F0 inputs (`evidence.f0`, feature `F15`)

`decide` passes `evidence.f0` and its own clock value to
`bridge_v2_switch_evidence.switch_activation`. Any refusal parks with the
adapter's stable code (`f0_*`). The block holds exactly these keys:

| Key | Content | Owner |
|---|---|---|
| `decision` | `dataclasses.asdict` of F0 `evaluate("F15", ...)` | F0, called by the trusted caller |
| `pins` | `trusted_policy_sha256` (from the operator-signed packet), `expected_head` and `expected_tree` (from the deployed bundle), `min_revocation_version` (the persisted high-water mark) | the trusted caller; never taken from the config |
| `document` | the activation config `{policy, signature}` as parsed | the trusted caller's one read |
| `revocation` | the `wd.bridge-v2-revocation.v1` state as loaded | the trusted caller's one read |

The adapter adds no authority.
- F0's refusal always wins: a Decision that is not enabled refuses first.
- An enabled Decision is never sufficient on its own. The adapter re-derives
  the pure part of F0's decision with F0's own `validate_policy`,
  `canonical_sha256`, `validate_signature`, `_parse_utc` and
  `_blocked_dependency`.
- The Decision's `policy_sha256` and `revocation_version` must match what the
  adapter re-derives.
- The adapter checks that the revocation state is at or above the high-water
  pin, bound to the pinned digest, fresh per the policy's
  `revocation_max_age_seconds`, not frozen, and does not revoke F15 or
  anything F15 transitively requires.
- The signed parameters come only from `policy.parameters.F15`, which holds
  exactly `tick_seconds`, `hysteresis_percent`, `evidence_max_age_seconds`
  and `budget_mode`. This layout is a proposal for the signing packet; the
  F0 policy at `482c3f0e` has `parameters: {}`.

The adapter cannot check three things, and they remain with F0 and the caller:
the kill-switch variable (F0 reads the environment and the Decision carries
the result), the file reads themselves, and the authenticity of the pins.

REQUIRED and NOT BUILT: an external trusted-caller provenance adapter. It
must:
- obtain `trusted_policy_sha256` from the operator-signed packet;
- obtain `expected_head`/`expected_tree` from the deployed bundle;
- persist `min_revocation_version` (and advance it to each Decision's
  `revocation_version`);
- call F0 `evaluate("F15", ...)` and read the config and revocation state
  once;
- assemble `evidence.f0` from exactly those values.

Until it exists, and until the operator signs a policy that enables F15, F15
is default OFF. No live caller or activation path exists.

## 4. Port signatures (existing, `wd_lane_relaunch_executor.py:73-92`)

`now`, `sleep`, `authenticate(request)`, `measure(lane)`, `processes(lane)`,
`observations(lane)`, `take_claim(lane, scope, lease_seconds) -> str`,
`release_claim(claim_id)`, `preflight_launch(lane, profile) -> list[str]`,
`checkpoint(lane) -> str`, `stop(lane, pid, started_at) -> bool`,
`launch(lane, profile)`, `resume_lane(lane, epoch, checkpoint) -> bool`,
`verify_catalog_signature(sha, signature) -> bool`, `read_record(lane)`,
`write_record(lane, record)`, `emit(event)`.

Port obligations for F16:
- `stop` returns `bool` and never raises. The executor calls it without a
  catch (`:516`), so the `Stop-VerifiedProcessTree` adapter maps every throw
  (including Tools replacement conflicts) to `False` with the cause recorded.
- The pid/start-time identity compares values from one source only.
- `resume_lane` returns `True` only on confirmed continuity; any exception or
  non-`True` value is "not confirmed" (`:567-568`).
- `verify_catalog_signature` returns `True` only on a verified signature;
  an exception is "unverified" (`:439-444`).

## 5. Journal phase and reason map (validated against the code)

Store: `tools/bridge_capacity_recovery.py`. `move(tid, expected, phase)` is a
compare-and-swap on the `transitions` row (`:223-230`) and appends every move
to the `journal` table with its reason (`:212-214`). Reaching `resumed` or
`cancelled_before_apply` deletes the pool reservation (`:231-232`); every
other phase keeps it held.

| Executor move | Reason written | Contract state |
|---|---|---|
| plan → `planned` | none | REQUESTED |
| `planned` → `quiesced` (`:501`) | none | QUIESCED |
| `quiesced` → `checkpointed` (`:506`) | checkpoint stored | CHECKPOINTED |
| `checkpointed` → `checkpointed` (`:513`) | `{"stop_intent_at": ts}` | CHECKPOINTED, stop intended (crash marker) |
| `checkpointed` → `cancelled_before_apply` (`:519`) | `source_stop_failed` | CANCELLED (terminal, reservation freed) |
| `checkpointed` → `apply_pending` (`:523`) | `{"source_stopped_at": ts}` | FENCED (source down, target not yet bound) |
| `apply_pending` → `verified` (`:532`) | JSON of the bound target epoch | APPLIED + VERIFIED (switch) |
| `apply_pending` → `apply_pending` (`:540,544,557`) | `rollback_failed:stray_unproven` / `:target_not_stopped` / `:not_verified` | HELD, operator required (lane may be down; reservation held) |
| `apply_pending` → `verified` (`:559`) | `rolled_back_to_previous` | ROLLED_BACK (never a successful switch) |
| `verified` → `resume_pending` (`:564`) | none | VERIFIED, awaiting continuity |
| `resume_pending` → `resumed` (`:574`) | `continuity_delivered` | CONTINUED |
| `planned`/`quiesced`/`checkpointed` → `cancelled_before_apply` (`:301`) | `executor_exception` | CANCELLED (source not down) |
| any → same phase (`:304`) | `executor_exception` | HELD, operator required (source may be down) |
| any pre-apply phase → `cancelled_before_apply` (recovery `:268`, `:276`) | `safe_boundary_or_binding_changed` / `source_profile_changed` | CANCELLED |

Derivation rules (binding for F16, F17, F27 and every reader):

1. **A failed resume is not CONTINUED.** On an unconfirmed resume the
   executor returns `failed` with `resume_not_confirmed, operator_required`
   and leaves the row at `resume_pending` (`:565-572`). A reader maps
   `resume_pending` to "VERIFIED, awaiting continuity" and, once the runner
   has returned, to HELD. Never to CONTINUED.
2. **The current row alone cannot tell a rollback from a switch.** After a
   rollback, `_resume` still moves `verified` → `resume_pending` → `resumed`
   with `continuity_delivered` (`:560`, `:564`, `:574`), which overwrites the
   `rolled_back_to_previous` reason on the `transitions` row. The outcome is
   therefore derived from the **journal**: SWITCHED only if the journal holds
   an `apply_pending` → `verified` entry whose reason is a bound-epoch JSON
   object; ROLLED_BACK if that entry's reason is `rolled_back_to_previous`;
   UNKNOWN (treated as HELD) if the journal is missing, unreadable or has
   neither. A rollback never counts as a successful switch, whatever the
   final phase.
3. **Self-moves carry state.** `checkpointed` → `checkpointed` and
   `apply_pending` → `apply_pending` are meaningful. A reader uses the latest
   journal reason, and a present-but-unparseable stop marker is UNKNOWN
   (as the executor itself does at `:394-395`), never ignored.
4. **Terminal states** are `resumed` (CONTINUED or ROLLED_BACK plus
   continued, per rule 2) and `cancelled_before_apply`. Every other phase is
   non-terminal and keeps the reservation held until a reconciling step or
   the operator moves it.

## 6. Fenced state and concurrency (F17)

- The only writer of a transition row is `RecoveryStore.move`
  (compare-and-swap on the expected phase). A lost race raises and must not
  be retried blindly; the caller re-reads and reconciles.
- F17 moves the journal onto the F8 lock order (runtime-root mutex first,
  then the per-claim lock) so the executor's claim and the work queue cannot
  interleave.
- FENCED (`apply_pending`) means the source is down or presumed down; the
  only exits are a verified target, a verified rollback, or HELD. A new
  intent for the same lane is refused while one is FENCED or HELD.
- An `identity_changed` notice is emitted when the bound session differs from
  the source epoch, and identity re-presentation is allowed only for a
  qualified resume.

## 7. Open facts (UNVERIFIED)

- `Ports` has no production implementation; every port behavior above is a
  requirement on F16, not an observed property.
- The runner's serialization and the intent directory layout are designs.
  They do not exist at base `8a7576af`. The F0 feature name for the switch
  policy is `F15` (F0 at `482c3f0e`, `FEATURE_NAME`).
- The trust flags that F15 still reads from injected evidence are caller
  assertions, not proofs: `requester.verified`, `catalog_signature_verified`,
  `binding.session_identity`, and each observed block's `observed_utc`. The executor's
  re-check at dispatch and before each side effect is the real gate.
- Cross-process timing of the journal and the claim files is not measured.
