# Bridge v2: one complete production package, one operator signature

Status: **implementation-planning proposal; not a signature, merge permission,
runtime activation or a claim that the package is implemented.**

Author: codex-lead-1. Requested and observed model/effort: not independently
measured for this document; native policy is not evidence of model identity.
No claim of satisfying the proposed highest-index composer rule is made.
Fable's independent review of this deliverable is required before plan closure.

## 1. Operator outcome and precedence

Operaattorin tavoite: yksi kokonaan toteutettu Bridge v2 -paketti, ei
tuotantoon jätettäviä stubeja tai pelkkiä varjotiloja. Koodaajat ovat Tools,
Lead ja Fable; riippumattomat testaajat RCO1 ja RCO2. Operaattori hyväksyy
jäädytetyn kokonaisuuden kerran ennen julkaisua, ei osakomponentteja erikseen.
Toolsilta saadut kiintiö-, neuvon hyöty- ja lisäkapasiteetti-ideat kuuluvat
samaan kokonaisuuteen. Turvaportit, veto-oikeus ja pysäytysehdot säilyvät.

This is an execution addendum to the plan and implementation map at
`c099c211a6fd71c109b6349e9e6ccd8794def297` (draft PR #1753):

- `BRIDGE_NEXT_WORK_PLAN_20260928.md`, SHA256
  `7269127dd6758199fbf432ed7db182466de68c5cdc7dcb61ba8541fe74f80178`;
- `BRIDGE_V2_IMPLEMENTATION_MAP_20260928.md`, SHA256
  `03bdb798af0752130156c74064ba66a816f908c6f7e648ea899ff923652ce6dc`.

Those documents retain design detail. For this proposed delivery, this
addendum replaces conflicting **delivery/ownership** wording: per-PR F8a
signatures, optional partial packages, Fable as sole Windows acceptance
tester, and stopping implementation at shadow mode. It does not override
deployed rules. Reconcile the companion documents before implementation freeze;
never distribute mutually contradictory active contracts.

Tools input: request `4a687ead-c2fb-4443-b8c4-4bdbd0b7300e`, canonical
2026-09-28T13:15:40.5052685Z; Lead review 13:17:25.4285258Z. These are design
inputs, not operator authentication. Current deployed baseline is c5f7c933.
PR #1754 (`87ad031a`) is a separate wake-control repair, not proof of F7/F30.
At implementation start resolve its merge state and import its exact tested
change once, with provenance, or consume it from main; never repeat a rollout.

### Meaning of "one package"

One integration PR, one frozen source tree, one evidence manifest, one
operator instruction, one release, one controlled rollout. Many internal
commits and branches are expected. File-disjoint implementation is parallel;
integration, shared-file changes and production side effects are serialized.
An unsigned component branch can be composed into the non-production
integration branch; it is **not merged separately to main**. Current manual
exact-head operator-signed merge gates remain unchanged.

This request authorizes preparation of this plan. It is not an exact-artifact
release signature. The future one signature explicitly authorizes the whole
reviewed code/policy scope and its bounded post-signature canaries and rollback.
No component-level approval dialogs are part of normal execution.
Credentials, paid services, new data egress, elevation and external prerequisite
installation are not silently authorized: discover blockers before signing.

## 2. Completion contract: no paper-only features

Each feature has states `specified -> implemented -> independently_tested ->
integrated -> pre_release_ready -> live_verified`. A branch commit, passing
unit test, mock port, queued wake, live PID or shadow recommendation is not
`live_verified`. A feature is complete only when:

1. Its production caller, adapter, schema, packaging and state recovery exist.
2. Success, refusal and fault paths have executable acceptance tests, with
   independent RCO evidence on the frozen integration head.
3. All required dependencies are present and verified for the deployed lanes.
4. A real bounded canary exercises the intended action and its receipt, then
   the feature is enabled for its signed steady-state scope.
5. Freeze, failed verification, crash recovery and rollback have been tested.
6. Runbook, operator status and metrics describe actual outcomes and limits.

All intended v2 capability bits are required in the release completion
manifest. Flags exist for staging/revocation, not to call unfinished code
complete. A mandatory feature that cannot reach its predicate blocks package
completion; it is not silently removed to obtain a green release.

Fail-closed is real functionality, not a stub: an unknown quota correctly
refuses an unsafe switch. But a deployment where every positive switch path
is permanently blocked by missing pool evidence does **not** satisfy the
automatic-switching outcome. Both positive and negative paths must be proven.
Likewise an installed Grok broker that has never completed an authorized real
consultation is not a verified Grok capability. A provider outage after a
successful qualification is degradation, not evidence that qualification never
mattered. Report current health separately from historical qualification.

No promise of never reaching a provider limit or uninterrupted future turns.
Report observed intervals, recovery time and lost-work bounds only.

## 3. Ownership and independent testing

| Lane | Production coding responsibility | Independent responsibility |
|---|---|---|
| Lead | Activation/receipts, wake/drain/contracts, launch/supervisor adapters, integration, packaging, release/restore | Reviews Fable/Tools boundaries; never self-grants an RCO pass |
| Tools | Quota provenance/meter, registry, Grok helper/broker, component doctor, observability, reproducible test runners | Reviews Lead/Fable contracts; supplies harnesses, not sole approval of its own code |
| Fable | Queue/locks/WAL, writer identity/binding, policy/router/composer/learning, continuation state machine | Reviews the completed plan and Lead/Tools integration; producer review is not an RCO vote |
| RCO1 | No normal feature implementation | Correctness/security; independent positive and refusal tests, state/identity/authority checks, exact-head verdict |
| RCO2 | No normal feature implementation | Independent adversarial, crash/replay/migration/rollback/interop tests and exact-head verdict |

Coders write unit tests with their code. RCOs independently design and run
acceptance/fault tests; they may own separate acceptance-test files, not the
production code they approve. Neither RCO relies on the other's verdict.
Findings go back to the physical-file owner. A change invalidates affected
reviews, and final integration approval names the final full head.

### Physical-file ownership (overrides ambiguous slice ownership)

- **Lead:** `ops/windows/reboot/**`, `BridgeTelemetry.ps1`, `Watch-Bridge.ps1`,
  `Monitor-AgentBridge.ps1`, new drain/exact-fetch helpers, `.agent-bridge/contracts/**`,
  `configs/bridge_v2_activation.json`, activation/packet/Windows-port/task-journal
  tools. Tools/Fable supply hook contracts; Lead alone edits installer,
  launcher, supervisor and packaging manifest, including their features' hooks.
- **Tools:** model/component registries, meter/collector/attribution/advisor,
  `wd_grok_helper.py`, broker/doctor/dashboard/qualification runners and
  `waggledance/core/bridge_workflow.py`. Fable proposes qualification semantics;
  Tools owns the harness implementation and shared advisory reader.
- **Fable:** `work_queue.py`, claim/lease/session/writer/schema/git-guard files,
  `BridgeEventClassifier.ps1`, policy/router/composer/learning, profile catalog,
  relaunch executor state machine, `bridge_work_ledger.py`, Rule 8 text.
  `Start-AgentBridgeSession.ps1` is Fable-owned; Tools supplies the doctor API.
- Each test file has its own named owner. Integration acceptance suites use
  disjoint RCO1/RCO2 directories. No "both own this file" assignments.
- The F18 PowerShell UI and F29 bootstrap/PS front ends follow physical owner
  Lead, although policy/doctor logic comes from Fable/Tools. This split avoids
  parallel edits to the same launch and operational files.

Before coding, record exact base/head, paths and dependency contract hashes in
one live claim per slice. If an unlisted shared file is discovered, stop its
edit and assign one owner; do not infer a broad wildcard write grant.
Existing dirty `C:\Python\project2` is preserved. Use clean persistent
C-drive worktrees from the chosen integration base; commit and push each green
checkpoint with `tools/savepoint.ps1`. No runtime bundles are development roots.

## 4. Dependency waves and ready work

Waves are engineering dependencies, not separate releases or signatures.

| Wave | Lead ready slice | Tools ready slice | Fable ready slice | RCO1 / RCO2 ready work | Exit |
|---|---|---|---|---|---|
| W0 contracts | F0 policy/receipt + release schema | Quota, advisory and component schemas; baseline capture | Switch/journal and queue transaction contracts | Threat model / independent fault matrix | Contracts and ownership reconciled; thresholds preregistered |
| W1 foundations | F1/F2/F5 hooks; early F28 real file transaction and rollback | F3/F4/F5/F6/F25/F29; real provider/provenance discovery | F8a/F8/F9/F10/F11/F12/F22/F23 | Cross-runtime locks and forged identities / crash cuts and missing dependencies | Foundation APIs, recovery and packaging tested, not mocked at integration boundary |
| W2 wake + explicit launch | F7/F30/F13 and F29 launch wiring | Qualification/meter datasets, Grok broker F20 | F15/F18/F19/F24 pure policy + bounded surplus | Injection/digest/authority / backlog/clock/launch host matrix | Positive and negative wake/launch paths pass |
| W3 actuation + learning | F16/F27 production ports + supervisor; finish F28 | F20 real broker, advisory outcome joins; read-only dashboard | F17/F26; queue migration/continuation integration | Freeze/owner fencing / crashes/partial CAS/stand-in + hand-back | Real isolated end-to-end adapter path and recovery pass |
| W4 compose + qualify | Single integration PR, deterministic staging/restore rehearsal | Full matrix runners and evidence manifest | Policy and contract reconciliation, qualification outcomes | Independently test complete exact head | All pre-signature gates green, no unresolved mandatory evidence |
| W5 signed rollout | Execute only the signed rollout transaction | Observe pool/receipt/dashboard reconciliation | Observe policy/continuation correctness | Verify each live stage, preserve independence | All required capabilities live_verified; publish final release |

F28 is built early enough to be rehearsed before signing, not improvised after
a failed installation. F8's journal/WAL and F0 freeze precede any real relaunch
or stand-in; switches never race old unfenced writers. F7 requires F1 baseline
and F2 contract; F30 requires F7. F15 requires measured F3/F21 evidence before
actuation; F16 depends on F5/F13/F15/F28; F17/F27 depend on F8/F10/F16.
F19/F20/F24/F26 feed a common quota reservation API, not independent budgets.
F23 origin enforcement and the protected authority channel in section 8.1
are hard prerequisites of every new privileged actuator, not late hardening.

Collect the required seven-day quota/reset history in parallel with code.
Elapsed development time cannot substitute for data. If usable existing
history has proper provenance it can qualify; otherwise the calendar wait is
a real dependency. Use one installed observer, not one collector per agent.
No busy model polling while a dataset or long test is running.

## 5. Full feature coverage and falsifiable acceptance

Codes Axx identify acceptance groups in the package manifest. Every row
needs command, environment, exact source/config hashes, raw result, independent
reviewer and a live activation/rollback receipt where applicable.

| ID | Owner | Complete deliverable (use original map for baseline paths) | Required acceptance beyond a unit stub |
|---|---|---|---|
| F0 | Lead | Signed-policy loader + durable freeze/revocation and versioned stage state | A00: missing/corrupt/stale/replayed policy refuses; running process observes freeze before next side effect; progress cannot self-grant authority |
| F1 | Lead | Wake telemetry and reproducible baseline/report | A01: replay recomputes counts/latencies/bytes; unknown tokens not fabricated; production before/after same workload categories |
| F2 | Lead | Bundled versioned role contract, hash checking, prompt lint | A02: fresh/resume/compaction reload and hash mismatch; old unpinned layers cannot override active contract |
| F3 | Tools | Registry v2, stored measured pool cost and authenticated provenance boundary | A03: seven-day/reset data within preregistered tolerance; shared pools not summed; stale/cross-account/residual-unknown refuse admission |
| F4 | Tools | Complete Grok attempt/cost/error ledger | A04: success/failure/timeout/manual/calibration all recorded once; stderr redacted; unknown observed model/cost remains unknown |
| F5 | Tools | Participant vs actual lock-holder evidence | A05: both shells and every writer accounted for; cross-logon/integrity or unknown holder blocks unsafe actuation, not merely an ACL printout |
| F6 | Tools | Dashboard from canonical receipts and pool snapshots | A06: restart/stale/corrupt/reordered evidence labels truthful; dashboard never writes authority |
| F7 | Lead | Durable wake backlog, drain acknowledgement, reconciliation | A07: enqueue/drain/crash cuts, duplicate bursts and rotation; no lost critical event; transport acceptance never means processed |
| F8a | Fable | PS/Python per-claim lock parity | A08a: both lock winners, timeout no mutation, release-wins never resurrects; real Windows cross-runtime concurrency |
| F8 | Fable | Queue-wide serialization, CAS, WAL/outbox and recovery | A08: one winner for overlapping scopes; kill before/after every write/flush/publish; replay gives original result; old writer fenced; no unlocked fallback |
| F9 | Fable | Git option/env/force guard with success twins | A09: `-C`, `-c`, alternate repository/env and unknown options cannot bypass target scope; allowed normal commands still work |
| F10 | Fable | Long-lived worker lease, token/session ownership, task retirement | A10: PS/Python claims agree; stale/PID-reused/finished owner cannot renew; live wedged owner stays held, never blindly stolen |
| F11 | Fable | Exact-request reply helper and requester supersede | A11: complete nonce/digest/responder binding, multiple answers, stale identity and duplicate/unbound replay corpus; no rewritten binding |
| F12 | Fable | Commit-status validation in both writers | A12: head-required statuses require full canonical SHA; malformed/case-collision payload rejected consistently, ordinary notices unaffected |
| F13 | Lead | Explicit per-lane profiles and enforced observed-model check | A13: each supported CLI/version, PS5 under PS7, invariant UTC and same-source PID/start-time; mismatch prevents writes and follows tested safe rollback |
| F15 | Fable | Pure switching policy + atomic shared-pool reservations | A15: guardrail/property corpus, concurrent admissions, reset/clock/freeze/quota changes; conservation and optional surplus raise use same bounds |
| F16 | Lead | One real Windows executor with safe ports and job containment | A16: real stop/start/verify/resume in isolated fleet; exception/timeout mapped to durable failure, not false success; each phase crash restart tested |
| F17 | Fable | Complete phase+reason continuity machine and fenced claim CAS | A17: in-session/qualified-resume/new-identity; rollback `verified` not success; `resume_pending` not continued; requester reissues; partial CAS repaired first |
| F18 | Lead + Fable API | Working `wd-model`/`wd-malli` UI with refusal explanations | A18: user commands enqueue through same executor; no direct apply path, no lane unfreeze, duration over cap refuses |
| F19 | Fable | Brief/delegate/plan-return router and outcome ledger | A19: committed plan ends burst at allowed boundary; exact artifact checked; escalation bounded; class conflict deterministic; advisory outcomes included |
| F20 | Tools | Serialized usable `grok_consult` broker with bounded read-only review | A20: actual authorized consultation+bound receipt; hourly attempts and future scoped exemption; wrong/relayed/replayed exemption rejected; snapshot/tool allowlist enforced |
| F21 | Tools, semantics Fable | Qualification harness and frozen receipts | A21: isolated held-out task classes, independent scoring, exact profile/version/effort, sample uncertainty and measured pool cost; expired/unqualified profile inadmissible |
| F22 | Fable | Claim schema discovery/preflight/help | A22: every supported resource kind has executable success+failure example; help does not widen accepted scope |
| F23 | Tools repro, Fable fix | Reserved-label/session-origin + wrapper-attribution closure | A23: both writers, registry/profile present/missing/mismatch; trusted internal callers migrated with visible outbox failure; non-repro needs independent corpus evidence, not silence |
| F24 | Fable | Eligible-first composer using comparable frozen evidence | A24: stale/incomparable/no eligible/top unavailable/tie/budget race; provisional output labelled; delegated composition cannot bypass admission |
| F25 | Tools | Per-pool quota visibility for all lanes | A25: age/source/account/pool and four provider states shown independently; urgency cannot override unknown-pool admission |
| F26 | Fable | Bounded learning, quarantine, admission and forecast updates | A26: independent deduplicated outcomes; negative/poisoned/replayed/self-graded cases; caps immutable; shadow-to-live demonstrated, not left shadow-only |
| F27 | Lead + Fable API | Crash/limit stand-in, WIP preservation, operation reconciliation and hand-back | A27: dead parent/live child/PID reuse/limit misbinding/unknown external write/partial WIP; one successor after complete fence; unknown effect HOLD; no inherited votes |
| F28 | Lead | Release worktree, crash-safe install and compatible bundle rollback | A28: interrupted pointer/file transaction recovers; state-version mismatch refuses unsafe old writers; never restore stale runtime data or erase WIP |
| F29 | Tools + Lead hooks | Usable component doctor/bootstrap/init with pinned sources | A29: clean clone/isolated user, each missing dependency including own runtime; feature/lane requiredness, no unattended install/elevation; deferred receipt publishes once |
| F30 | Lead | Short typed wake, exact canonical fetch, durable bounded digest | A30: replies/late corrections/veto/HOLD/cancel without ID/mislabelled informational/injection/hash mismatch/overflow; real same-conversation delivery and corrected consumer tests |

F14 is a numbering gap, not a hidden feature. F8a is an internal milestone of
F8, **not a separate main PR/signature** for this package.

### Previously deferred items: explicit disposition, no silent leftovers

- S10 identical unbound replay and PS/Python casing ambiguity: include closure
  in A11/A12/A23. Define canonical case/duplicate-key handling and event
  deduplication identity in W0. Preserve raw historical events; strict new
  writes reject ambiguity. A compatibility reader quarantines ambiguous
  legacy evidence, never upgrades it to an approval. Legitimate distinct late
  corrections must not collapse just because request_id is shared.
- Hung **live** owner: detection, visible safety HOLD and file-disjoint progress
  are required completed behavior (A10/A27). Unproven forced takeover is not
  part of the product guarantee. This is not a placeholder recovery adapter.
- Rule 9b activation, Stage-2 domain cutover, approval carry-forward, paid tiers,
  new providers/data destinations, cryptographic operator authentication,
  cross-logon/low-integrity actuation and unrelated PR-10/14/16 frameworks remain
  explicit non-goals. None is an undeclared dependency of required v2 behavior.
  If implementation discovers such a dependency, stop package freeze and
  resolve scope; do not ship a fake implementation or quietly weaken a gate.
- Alternative transport (including app-server) is not required by name. The
  currently selected transport must pass the real delivery/ack/recovery tests;
  if it cannot, F7 is blocked until a supported adapter is implemented and
  reviewed. Do not use UI focus or manual operator relay as the success path.
- Historical malformed rows are preserved and visibly quarantined, not
  rewritten into passing evidence. Branch protection is reported, not assumed.

## 6. Contracts fixed before parallel implementation

Store versioned schemas and golden cross-language examples under one W0
contract inventory. Proposed filenames are deliverables, not files claimed to
exist today. Each API has bounded inputs, stable error codes, unknown state,
UTC timestamps with invariant parsing, schema version, provenance and hashes.

| Contract | Producer -> consumer | Required fields / invariant |
|---|---|---|
| Activation + receipt | Lead -> every actuator | packet_id, candidate tree/head/base, artifact hashes, policy version, explicit capability bits, absolute qualification deadline, revocation version; detached operator receipt, no self-signed JSON |
| Quota snapshot | Tools -> Fable policy / Lead executor | account+pool+limit/reset epoch, remaining lower bound, observation/expiry UTC, source, uncertainty/residual, in-flight reservations; unknown never numeric zero or infinite capacity |
| Reservation | Fable policy -> executor/broker | key, pool/reset epoch, upper-cost bound, snapshot+policy digest, task revision, terminal state; atomic compare+reserve; crash/replay cannot refund an attempted cost blindly |
| Switch intent | Fable/UI -> Lead executor | unique id, actor/origin, lane, target, expected process/bridge generation, checkpoint/task revision, quota reservation, policy digest, expiry; no free-form authority claims |
| Switch result | Lead ports -> Fable journal | phase AND reason, source/target PID-start identity and observed profile, side-effect receipt, verification and continuation separately; `rolled_back` never counted as successful apply |
| Queue transaction | Fable -> PS/Python writers/outbox | runtime-root identity, queue then per-claim lock order, owner generation/token, revision CAS, operation key, durable WAL/outbox state; no archive resurrection |
| Wake/drain | Lead -> existing lane conversation | lane+generation, delivery id, canonical event hashes, contract hash, cursor epoch; hints not authority; handling receipt before processed watermark |
| Advisory outcome | Tools/Fable -> learning | consultation/request/prompt/task/artifact digests, observed profile, suggestion disposition and independent evidence, cost/latency and attribution limits |
| WIP/operation journal | Lead/Fable -> stand-in | task/base/head/dirty inventory and hash, safe secret-exclusion, external idempotency key, attempted/applied/verified/unknown; reconcile unknown before replay |
| Component capability | Tools -> launch/install | supported platform+lane+feature, pinned detection/install source, CLI/auth/quota/observed-turn separately, bootstrap receipt id; found is not trusted |

Source files never embed their own future commit hash or a signature over a
tree that includes that signature. Freeze code first, produce detached evidence
and signature envelope naming the exact head/tree, and verify the detached
receipt when loading policy. Any catalog field that currently expects an
inline signature must be migrated/tested to this non-circular verifier; a
placeholder `approved:true` is not a signature.

## 7. Tools proposals included as implementation, not commentary

### 7.1 Measured pool admission (F3/F4/F15/F20/F25)

Tokens, API prices and a successful call do not prove remaining quota. Use
provider/account/pool/reset-bound observations with source, freshness and
uncertainty. Cross-product usage and unobserved consumers are an explicit
residual. A calibrated estimate is admitted only when its conservative bound
meets the frozen evidence rule; unbounded residual means unknown.

Atomic decision: conservative remaining lower bound minus existing in-flight
reservations minus forecast work through reset minus incident/reviewer reserve
minus uncertainty margin must cover the proposed upper-cost reservation.
Measure units per pool; never add two Claude/Codex lanes as independent pools.
Revalidate immediately before dispatch. Reset/identity changes invalidate stale
snapshots; they do not erase unfinished operations or create free allowance.
If no safe positive path can be measured, automatic switching is a blocker,
not a guessed cost table. Grok remains an advisory service, not a lane-switch
target; measurable quota alone does not grant new actuator authority.

### 7.2 Advisory outcome and benefit (F4/F19/F20/F21/F26)

Every attempt, including failed/skipped/unhelpful advice, has a consultation
record and a later outcome join: suggestion IDs, used/rejected/unused/unknown,
reason, independent correctness evidence, changed artifact, measured pool
consumption (or unknown), latency, evaluator independence and version.
Do not reward successful HTTP/CLI exit, long output, tokens or adoption alone.

Separate three questions: was it used, was it correct, did it add incremental
value? The third requires matched task-class/version baselines or paired
isolated/held-out comparisons, uncertainty and independent scoring; otherwise
label benefit unknown/correlational. Missing outcomes stay in the denominator.
Deduplicate replay, reject self-grading, expire drifted evidence, and record
negative outcomes. Advisory learning cannot qualify an unqualified lane model.

### 7.3 Useful surplus capacity (F15/F19/F24/F26)

Surplus is not unused quota. After reservations and reset-horizon demand,
consider a stronger admitted profile only if independent task-class evidence
shows sufficient marginal quality benefit per measured extra pool consumption.
Use the same executor, signed thresholds, floors, reviewer independence,
dwell/hourly/pool caps, burst deadline and plan-return rule as conservation.
No change when benefit is unknown or negligible, even near a reset. No
use-it-or-lose-it spending, speculative new provider, or silent effort upgrade.

### 7.4 Preregistered acceptance parameters

W0 freezes an evidence protocol before collecting results: task classes and
held-out corpus, minimum repeats/sample size, quality floor/non-inferiority
margin, confidence method, allowed quota-error bound, TTL, reset coverage,
max digest delay, latency SLO, retry/circuit bounds, reserves and exploration
budget. RCO1/RCO2 review it independently. All values and units must be
numeric/explicit in the eventual packet; no `auto`, unset default or value
chosen after looking at favorable results. Use the original proposed 70/90/95
trip lines, 30-minute dwell, four switches/hour and two-hour burst as starting
policy constraints, not measured facts or current runtime settings.

Seven days of pool data and a measured reset cycle are minimum data coverage,
not evidence of universal accuracy. RCO acceptance checks distribution/task
coverage and uncertainty, not just elapsed time. Exact numeric deployment
thresholds cannot honestly be invented from today's unverified pool bindings;
failure to freeze/validate them blocks W4 and the signature packet.
Use disjoint W1 calibration and W2 held-out evaluation windows. Preregister
their dates/sampling protocol and the provider/account-page observation method;
do not fit and validate the forecast on the same seven days. A confidence bound
computed from selected successes is invalid. Report missed predictions too.

## 8. Independent verification and release blockers

RCO1 owns the authority/identity/concurrency/receipt positive-and-negative
matrix. RCO2 owns the independent crash/replay/migration/transport/interop
matrix. Both inspect the complete integration diff and may veto any feature.
Tools builds runners; each RCO selects and runs independent cases. Coders fix
findings; tests never become a producer's substitute for independent review.
Both RCOs review each safety-critical slice during development, then both
review the complete frozen composition (not only a delta). Keep slice commits
and byte-identity/diff maps as review aids; they do not carry old approvals
forward. If an RCO authors an acceptance test committed into the candidate,
the other RCO reviews that test and the authorship is recorded. The author
cannot supply the sole independent acceptance evidence for that test's claim.

### 8.1 Security and lifecycle decisions from independent RCO review

These are additional **required implementation work**, not assertions that
the present shared-account deployment enforces them:

1. **Unconditional fixes versus optional actuation.** F8/F8a/F9/F10/F11/F12/
   F22/F23 safety checks are unconditional code in the new bundle. F0 may not
   disable their lock/identity/validation protections. Its schema rejects
   entries attempting to flag them off. Missing activation state disables new
   automation, not security. Test corruption/deletion of policy while C2, git
   bypass and reserved-label probes remain refused.
2. **Mixed generations.** Add protocol versions to queue/WAL ownership and
   writer registration. Before a new writer mutates, prove there is no live
   pre-F8 mutation-capable participant, including scheduled jobs and children.
   Cold-quiesce writers globally; roll sessions one at a time with mutation
   held until compatibility and complete fencing are demonstrated. RCOs can
   remain available read-only. Unknown writers mean HOLD. Test actual old+new
   binaries together, not two new mocks. Python uses Windows `CreateFileW`
   share mode zero for the same sibling lock as PowerShell `FileShare.None`;
   ordinary Python `open()` is not sufficient lock parity.
3. **Freeze cannot be lane-writable authority.** A lane may request a monotonic
   freeze, never grant/unfreeze itself by editing JSON. The new implementation
   must separate privileged grant/revocation state from the shared runtime
   directory. Select a dedicated Windows authority-guardian service identity,
   with an ACL-protected monotonic revision store not writable by any lane
   token; the operator-control endpoint is distinct from the lane intent
   endpoint. Existing same-account scripts alone cannot satisfy this boundary.
   Bind dispatch permissions to guardian-issued bounded capabilities and
   verify caller token/session provenance at the endpoint. Guardian launch,
   credentials/installation prerequisites and least-privilege ACLs are F0/F5/
   F29 deliverables, owned Lead (service) and Tools (doctor). No new credential
   is fabricated or collected by agents; unavailable protected provisioning
   blocks the package. The one release signature covers reviewed deployment
   of this component, but does not remove OS setup/elevation requirements.
   Prove a lane can neither edit grant state nor use the operator endpoint;
   detached/scheduler-spawned lane children must not bypass it. A compromised
   administrator remains outside the threat boundary. Document residual
   shared-account impersonation risk explicitly; do not market UUID checks as
   cryptographic authentication. The guardian design/ACL proof is a W0 security
   decision gate before any privileged implementation is called ready.
4. **Origin and authority.** F23 enforces session-origin checks in both writers.
   Executor actor is derived from a verified session/capability, never
   `intent.actor` text. Forge another member/principal/operator and verify no
   side effect. New grant/unfreeze and Grok exemption records use the protected
   operator channel; conversational or peer-relayed text is not a grant.
   The current direct-text release audit remains honestly labelled observed
   instruction, not cryptographic proof. No unsigned runtime exemptions now.
5. **Reviewer protection.** No automatic switch, effort change or stand-in of
   an RCO holding an open review request/claim. Derive busy status from canonical
   requests/claims and source identity, not intent prose. On RCO outage the
   other RCO may do independent work, but this package's two-pass gate waits
   for both; one cannot substitute for the missing vote. Preserve old-session
   vetoes in the package receipt: a replacement's fresh pass is not clearance.
   Record actual model/effort or unknown with review execution evidence;
   routine subagent defaults never lower reviewer floors. Explicit operator
   replacement requires reconciled review assignments, not vote inheritance.
6. **One external actuator.** Remove the actuating buddy fallback. Principals
   may request recovery/HOLD; only the supervisor can restart the same external
   executor with exclusive ownership. No lane-spawned alternate stop/start
   path. The executor's bounded job contains transient helper processes, not
   the long-lived target lane: the supervisor owns target launch/lifetime.
   Test closing/timing out the executor job leaves the verified target alive.
7. **Cross-store crash table.** Specify cut points spanning SQLite recovery,
   claim files, outbox and identity_changed events, not just file writes.
   Write intent before stop, migrate by scoped revision CAS, emit using the
   same idempotency key, reconcile after each cut. A swallowed sweep-writer
   failure must leave a durable outbox obligation or refuse the archive.
8. **Independent stage acceptance.** Each stage has a deterministic checker
   bound to packet+generation+raw evidence digests. After bounded canary
   permission, both RCOs independently rerun it and attest before steady-state
   promotion. Missing/stale/other-generation attestations prevent promotion.
   These are agent test attestations, not more operator signatures. No stage
   owner self-grades its own permanent activation. Destructive fault injection
   uses isolated decoy processes only; live probes are explicitly bounded.
9. **Rollback is not an event-triggered fleet kill.** A lane event can trigger
   safe admission-off/HOLD, not automatic bundle restore. Restore requires a
   preauthorized deterministic integrity/recovery predicate independently
   checked against protected evidence, plus writer fence, outbox preservation
   and schema compatibility. Same-account file hashes alone are not a hostile
   lane security boundary. c5f7 restore would reintroduce known safety defects;
   list those regressions explicitly in the packet and prefer safe admission-off
   with the fixed substrate retained. Never automatically resurrect unsafe old
   writers or erase new data to make rollback appear successful.
10. **Delegation continuity.** Bind rollout delegation to the receiving Lead
    identity/session and packet, not the name alone. Freeze autonomous changes
    to the rollout coordinator for the transaction. The planned Lead rollover
    must use a qualified, identity-preserving continuation with checked intent
    and journal; otherwise stop for a replacement whole-package authorization.
    A new stand-in never inherits the signature. During qualification, automation
    is limited to explicit canary targets, never the coordinator or an RCO
    reviewing the work. This avoids the circular rule of enabling nothing until
    all live activation tests have already passed.
11. **Data and child capabilities.** Explicitly list bounded xAI repository
    review egress in the future packet; do not hide it behind a generic
    "no new egress" statement. Snapshot allowlist+inventory digest exclude
    secrets, runtime/audit state, identity/config data and unapproved files;
    tracked does not mean safe. Pin and test actual Grok CLI behavior. Cheap
    child executors receive isolated worktree and scrubbed bridge/GitHub
    credentials; this is least privilege, not a claimed strong sandbox against
    a hostile same-user process. No new provider or broader data class is added.
12. **Authority discoverability.** Add a versioned in-tree authority contract
    referenced by Rule 8 and the role contract, naming every bounded permission,
    channel and revocation. Lint against the packet. No policy lives only in
    a forgotten signing conversation. Contract-hash failure stops the affected
    lane and the rollout, not a blind fleet-wide stop; prove usable rollback.

Pre-signature evidence is isolated real adapters/provider probes within existing
authority, full matrices and rehearsals. Post-signature evidence is actual
production canary/continuation/stage attestation. The feature ledger has separate
columns for these, so signing never claims unobserved production results.

Required environments: Linux CI on its supported Python matrix and real Windows
PowerShell 5.1/7, direct PS5 child from PS7, supported CLI versions, exact pinned
package. Isolate runtime roots, kernel names, ports, accounts/data destinations,
fixtures and process jobs. Scrub test identities without replacing the active
lane's inherited bridge pins. Never run forged messages against production.
One supervised full-suite run at a time per resource budget; record parent and
child PIDs and unique logs; cancel only owned test processes and verify exit.

Local iteration uses `python tools/select_affected_tests.py --files <paths>`;
if it says full suite, run full suite. CI always runs full suite. Reuse only
exact-head/config/environment-matching evidence. Final Windows and Linux
evidence both bind the frozen head; no carry-forward over a changed head.

The evidence ledger records requirement -> feature -> source/adapter -> test
-> result -> independent reviewer -> production probe -> rollback probe.
No unexplained skips, swallowed exceptions, silent fallback, unknown required
field, unreviewed stub/NotImplemented path or required feature permanently off.
Static stub scans are useful but do not replace actual call-path tests.

Block W4/signing for: absent required data/provider capability, unresolved
ownership/Tools CONFLICT, missing independent reviewer, missing required code
path or bundle file, failed test, unresolved veto, policy mismatch or unknown
non-idempotent effect. Complete safe unrelated slices while blocked; do not
turn the blocker into a component waiver or partial release.

## 9. One operator signature and exact-artifact gates

Do not ask for a signature until the composed candidate is complete and its
pre-signature verification is green. Internal planning/code reviews and RCO
passes are agent evidence, not additional operator signatures.

The detached packet contains:

1. Integration PR/repository, full head/tree/base SHAs, diff inventory, feature
   matrix, tag and release-note SHA256; previous production bundle identity.
2. Evidence manifest hashes, exact-head Linux and Windows results, RCO1 and
   RCO2 passes, Lead and Tools build consensus, no-veto and accepted-queue
   checks from current canonical tools. Fable producer review is additional.
3. All policy/contract/catalog/component/install-source/activation/runbook
   hashes and every proposed authority explicitly enumerated: bounded member
   intents/admission/learning, qualified stand-ins and scoped Grok exemption.
4. Detached signature audit (literal direct operator instruction, channel and
   time, UTF-8 hash, packet hash), never described as cryptographic identity.
5. Delegation for one exact squash merge, matching-main-tree verification,
   main CI, packaging, stage/install, ordered lane rollover, bounded canaries,
   staged activation, final tag/release publication and tested rollback.
6. Absolute qualification deadline (at most 14 days from the signed timestamp),
   stage predicates, mandatory capability bits, stop conditions, downgrade
   compatibility and revocation/freeze behavior. No per-stage re-signing.

Implement two layers to avoid a hash cycle: an in-tree canonical content/policy
manifest contains head-independent artifact digests; a detached canonical
envelope binds that manifest, final head/tree/base and exact-head evidence/RCO
receipts. The operator signs the envelope digest once. A later signature audit
is outside the reviewed tree. Every delegated step rechecks all relevant input
digests; a one-byte substitution in any bound artifact refuses before mutation.

Use deployed pinned gate code, never gate code supplied by the candidate to
approve itself. Mechanically require separate `check_rco_pass_present` checks
for `claude-rco-1` and `claude-rco-2` (with actual PR author exclusion), plus all
peer veto checks, Lead/Tools consensus and accepted-queue checks. An aggregate
"any recognized RCO" check is insufficient for this package. Record resolved
helper paths and raw verdicts. Current gate deficiencies are not fixed by prose:
additional package checks can only tighten, never replace or weaken, those gates.

The signature is one approval of all listed scope, including denylisted files;
it does not alter the merge gate or authorize admin/force. Just before merge
recheck exact head/tree/base and complete gates. Invoke only the existing
manual path with `--squash --match-head-commit <signed-head>`, no gate bypass.
The merged main tree must equal the signed tree and its parent the signed base;
both main CI workflows must pass before installation.

A changed head/base/policy/notes/artifact invalidates the packet: revise and
retest the whole packet, then request one replacement package signature if
needed. "One signature" cannot honestly authorize unknown later code. No
re-signatures for unchanged subcomponents or normal preauthorized stage
progress; failures HOLD/rollback instead of asking for piecemeal exceptions.

## 10. One rollout transaction, completion only after live evidence

1. **Before signature:** reproducible candidate bundle in isolation; every
   packaged entrypoint/import/hook/schema/contract included; install, cold boot,
   mixed-generation fencing and rollback rehearsed on non-production state.
   Non-production real CLI/provider probes use existing lawful authority and
   budgets only. New production authority is tested with simulated policy,
   never against the real fleet before signing.
2. **After signature:** exact merge/tree/base and main CI checks; named pushed
   release worktree. Stage-only DryRun then stage. Verify recursive file list
   and hashes against the candidate, with explicit provenance mapping for
   generated metadata (merge commit differs from PR head, tree does not).
3. **Cold install:** fence old mutation-capable writers and drain/reconcile
   pending transactions. Pause supervisor scheduling for the install and
   verify its owned invocation exits. Install hash-pinned bundle transaction,
   verify wrappers/tasks/integrity and reboot pointer, restore scheduling.
   RCO sessions remain available; quiescing writes is not stopping both RCOs.
   Protected guardian provisioning and negative ACL tests must pass before any
   new authority is activated; installation cannot infer trust from a PID.
4. **Lane rollover:** RCO1 -> RCO2 -> Tools -> Lead -> Fable. Checkpoint/idle,
   exact identity stop, DryRun launch, launch with manifest hash, observed
   profile/generation, sessions/transcript growth, bound bridge round trip,
   ACL/logon/holder checks and scheduled-supervisor/weekly-task pins per lane.
   Never both RCOs down together. Do not repeat the historical transcript
   archive permission; fresh-start/archive needs its own stated necessity and
   scope. Normal qualified continuation is the planned path.
5. **Activate dependency stages:** measurement/doctor/contracts, queue safety,
   wake/drain, explicit launch, then policy/actuation/continuity/learning.
   Wake canary: Tools (verified ownership), Lead, one Claude lane, remaining
   lanes individually. Switch/stand-in canary: Fable with Lead observing and
   both RCOs available, then Tools/Lead, then RCOs only when not reviewing the
   affected work. This separate functional-canary order does not reorder the
   bundle rollover. Every stage uses the same signature and frozen predicates.
6. **Verify positive live use:** at least one completed required path per
   supported lane/provider/actuator and declared task class; bounded actual
   model/effort transition and return, real advisory result/outcome, real wake
   round trip/late correction, bounded learning update and independent
   verification. Exercise safe negative paths without destructive live fault
   injection. Destructive crash cuts belong to the isolated rehearsal.
   RCO1/RCO2 independently attest the stage checker's packet-bound results;
   only then promote the bounded canary into its signed steady-state scope.
7. **Finish:** only when every mandatory stage is live_verified, publish the
   signed tag/release notes and final production evidence digest. A draft
   release may be prepared earlier but is not publicly represented as complete.
   Update compact states and the verified reboot pointer. A reboot alone
   neither selects a newer Git branch nor grants activation authority.

Failures stop admission, record phase/reason and execute the signed compatible
rollback. Revocation is durable and checked before side effects. Do not roll
queue/WAL state back to unsafe old writers; refuse incompatible rollback and
HOLD with preserved data. Never overwrite WIP, replay unknown external effects
or automatically unfreeze an operator HOLD. If qualification expires unfinished,
stop new intents and safely settle/HOLD; no timer-reset renewal by reboot.

## 11. Handoff and plan-review checklist

Before declaring this plan implementation-ready, obtain Tools' contract gaps,
RCO1/RCO2's independent test gaps and Fable's own review of the **final exact
document head**. Record objections, their disposition and any remaining blockers;
queue acceptance is not a review. Amendments require re-review of the final
artifact. The existing PR #1754 verification continues separately and its source
claim must not be stolen by this planning work.

Implementation kickoff then creates the W0 contract inventory, requirement/
evidence ledger, explicit physical-file assignments and preregistered thresholds.
W1-W3 cannot bypass unresolved contract prerequisites. Each bounded slice ends
with exact commands/results, green commit+push, checkpoint and durable handoff.
No portion is called production-ready merely because this document is detailed.
