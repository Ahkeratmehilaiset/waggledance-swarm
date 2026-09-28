# Bridge v2 package plan (after #1751)

Status: **joint design proposal of fable-5 and Lead, confirmed after
brainstorm round 3 (Lead, 04:44:02Z, reviewed head ecb3a643).**
- Nothing here is implemented, authorized or activated.
- No runtime path described here exists unless it is labelled MEASURED.
- It grants no authority and changes no gate.
- Implementation starts only after the #1751 rollout is verified, and only on
  an explicit assignment.

Authors: fable-5 (text), with Lead's round-1 and round-2 positions
incorporated (§7).

## 0. The operator's directives (2026-09-28, verbatim including typos)

> välitä lista leadille sellaisilla muuttoksilla että mahdollisimman käyttäjä
> ystävällinen mallin vaihto on mahdollista ja ne kekskeneräiset ovat sinun
> suunnittelemana suunniteltu loppuun ja miten ne istuvat nykyiseen. Bridgen
> pitää olla tietoinen jokaisen eri mallin käyttörajasta ja kustannuksesta,
> Grok pitää olla käytettävissä ja ja kaikki mallit niin etteivät ne
> perustyössä käytä kalleinta mallia mutta suunnitelu ja brainstormeissa
> pystyvät käyttämään mallia joka on tehhokas.

> Niin tai tässä kokonaisuudessa täytyy olla toiminnallisuus, että esim fabel
> tai lead voi vaihtaa mitä tahansa mallia tai mallin efforttia perustuen
> pyyntöön, omaan arvioon tai mittaukseen. Eli parven päämiehillä on
> mahdollsuus autonomisesti säätää toimintaa ja heillä pitää olla tiedossa
> jokaisen olemassa olevan mallin äly kustannus ja jos omaa tilaa ei pysty
> muuttamaan sen muutostyön voi pyytää toiselta mallilta. Ja sitten perustyö
> pitää pystyä arvioida mihin malli kannattaa siinä muuttaa että toiminta on
> kustannustehokasta, mutta parhaalla mahdollisella alustuksella

> brainstormaa leadin kanssa 3 kierrosta ja tee yhteenveto lopuksi
> kokonaisuudesta nyt tehdään kaikki parannukset kerralla ja mahdollisimman
> vähillä allerkijoituksilla ja stepeillä

> brain stormiin tulee ottaa mukaan myös muut keskeneräiset stepit mitä esim
> lead ja sinä löysitte bridgeen liittyen ja samaan toteutus kokonaisuuteen

> voisitko tuonne brainstormeihin ja sprint planeihin toteuttaa säännön että
> sen koostaa aina se tehokkaista malleita jolla on sen parven paras mitattu
> älykkyys ulkopuolisten raporttejen mukaan

> Ei ole mitään tunniin kiintiörajaa jos minä kysyn ja jatkossa parven täytyy
> tietää toistensa kiintiö

| # | Requirement (English restatement) | Where |
|---|---|---|
| R1 | Model switching is as user-friendly as possible | §2.2, §2.3 |
| R2 | The bridge knows every model's usage limits and cost | §2.1 |
| R3 | Grok is available to every lane | §2.6 |
| R4 | Routine work avoids the most expensive model; planning and brainstorming may use a strong one | §2.7 |
| R5 | The unfinished items are designed to completion and fit today's code | §2.8, §2.9, §3 |
| R6 | The principals (fable-5, Lead) switch any model or effort by request, own assessment or measurement | §2.2 |
| R7 | The principals know every model's intelligence and cost | §2.1 |
| R8 | A lane that cannot change its own state asks another to do it | §2.4, §2.5 |
| R9 | Routine work goes to the most cost-effective model, prepared with the best initialization | §2.7 |
| R10 | Everything at once: every open bridge item Lead and fable-5 found, with the fewest signatures and steps | §3, §4 |
| R11 | Brainstorms and sprint plans are always composed by the swarm's model with the best externally measured intelligence | §2.10 |
| R12 | No hourly Grok limit applies when the operator asks; every lane knows every other lane's quota | §2.1, §2.6 |

## 1. Current state

Evidence labels:
- **MEASURED**: observed on this machine or in the bridge, 2026-09-28, main
  c5f7c933.
- **DOCUMENTED**: written in a repo doc but not reproduced here.
- **SUSPECTED**: seen once or not yet reproduced; it must be reproduced in
  isolation before anyone calls it a bug.

**Model selection**
- MEASURED: every lane launches with `model: native` and `effort: native`
  (`ops/windows/reboot/wd-fleet.json`).
- DOCUMENTED: a `/model` in one window becomes the default for sibling lanes
  (`docs/BRIDGE_EFFECTIVE_MODEL.md`).
- MEASURED: the lane catalog is unsigned, and every profile has
  `approved: false`.
- MEASURED: the pacer, registry, planner, relaunch executor, capacity advisor
  and cost meter are all shadow or advisory. The executor has no production
  ports.
- MEASURED: the registry has benchmark indices and `usd_per_task` only. It has
  no Haiku rows, no Grok rows, and no limits, pool or measured cost.
- MEASURED: the collector writes `account_pool = None`.

**Grok**
- MEASURED: the only live path is the hourly CLI helper (one attempt per
  60 min, fleet-wide).
- MEASURED: "Lead only" is a prompt rule, and results always go to Lead.
- MEASURED: stderr is dropped, and no tokens or model are recorded.
- MEASURED: `waggledance/core/bridge_workflow.py` refuses to route Grok.

**Wake delivery** (Lead's audit,
`C:\Python\project2\.codex-audit\swarm-wake-audit-20260928.md`)
- MEASURED: there is no backpressure beyond a 5 s debounce.
- MEASURED: visible no-op turns.
- MEASURED: contradictory instruction layers.
- SUSPECTED: an old queue backlog draining as redundant turns.

**Work queue and guards**
- MEASURED (code read): the scope check-then-write race across task ids
  (the #1567 class). B7's CAS is an identity check, not a transaction.
- MEASURED: the git leading-option bypass (`-C`, `-c`, `--no-pager`) on
  d26357e1 and bbb855bf.
- MEASURED: Lead's rollout claim auto-expired after 532 s, because its
  heartbeat was bound to a short-lived shell.
- MEASURED: a request's `expected_responders` freezes the responder's
  uuid, session and run. After an identity change, a reply is refused.
- MEASURED: B7 claims are owned by a session and token.
- MEASURED: Lead's 04:08Z claim failure (`unknown resource kind`) was a caller
  error; the helper correctly refused an unsupported resource kind. The gap is
  discoverability.
- SUSPECTED: a per-lane wrapper misattributes execution evidence. The Lead
  path is fine today, but the `start-wd-tools-consumer` branch is unverified.
- SUSPECTED: a writer may not refuse an unbound reserved `agent=operator`
  event when the registry or profile lacks an entry (`Write-AgentEvent.ps1`
  437-499).
- MEASURED: a scheduled task's time limit does not reach a
  `wd_silent_launch` child.

## 2. Design

### 2.1 Model knowledge: registry v2 and the model table (R2, R7)

**Registry v2 fields**, all with provenance and freshness:
- `pool` / `limit_id`, from validated provenance only;
- measured pool points per Mtok (PR-13 meter, stored daily);
- tier (economy, standard, strong, premium);
- context window;
- quality per task class, from F21 receipts;
- a benchmark score as task-specific evidence with its version (never a
  universal rank).

API price is not quota cost and is not used as one.

**`wd-model models [--json]`** gives one row per model and effort with:
- those fields;
- pool state (used, reset, forecast);
- CLI availability;
- membership of the signed envelope.

Unknown values are shown as unknown. This table is the principals' decision
input, and a compact copy goes into their boot brief.

**Shared quota visibility (R12).** Every lane, not only the principals, can
read every pool's state: each Claude and Codex pool, and Grok's weekly pool
as far as it is measurable (F4 ledger tokens, calibrated against the
operator's readings of the account page).
- It is a read-only snapshot (`wd-model status --json`, and a compact line in
  each lane's boot brief and wake notification), with its age and source.
- A lane checks a peer's pool before it sends that peer work or a
  `grok_consult`; a peer in `conserve` or with an unknown pool gets only
  urgent work, and the sender says why.
- Reading another lane's quota grants no authority over it; switching stays
  with the executor (§2.4).

### 2.2 Who may switch, and within what (R1, R6)

**The envelope.** The operator-signed set of profiles that automatic or
principal switching may use.
- A profile enters only with an F21 receipt that covers both quality and
  quota cost.
- A profile whose cost cannot be measured stays out. A smaller, measured
  envelope is preferred to invented values.
- Fast, priority and pay-per-use credit tiers are never in it.
- Grok is outside automatic model switching, because its pool cannot be read.

| Channel | May do |
|---|---|
| Operator's own terminal (`wd-model`) | `set`/`reset` within the envelope with expiry; `freeze`/`unfreeze` |
| Operator's direct words in a lane's own window (session-observed; the originating session and the literal instruction are recorded; never claimed as cryptographic authentication) | `set`/`reset` within the envelope with expiry; `freeze`. `unfreeze` only from the operator's own terminal or direct input |
| Principals (`codex-lead-1`, `fable-5`), triggered by a request, a written assessment or a measurement | submit a bounded **switch intent** for any lane, including themselves, to the executor (§2.4) |
| Any other lane | a `profile_request` to a principal |
| A peer relay of "the operator said", an environment identity, or an agent-composed `operator` event | nothing |

**Mechanical guardrails** (enforced in the executor, not by goodwill):
1. **Budget.** The projection must stay within the trip lines (70 % steady,
   90 % burst, 95 % sprint). The premium tier is only a time-capped burst.
2. **Unknown.** Unknown cost, quota or quality means HOLD. There is no assumed
   downgrade; a switch needs a known, qualified target and keeps reviewer and
   task-class floors.
3. **Rate.**
   - Dwell of 30 min per lane.
   - At most 4 switches per lane per hour.
   - One change per pool per tick.
   - Hysteresis for raises.
   - A revert skips all of these.
4. **Reviewer independence.**
   - RCOs choose their effort within their range.
   - Nobody lowers an RCO below the reviewer default.
   - A principal never changes an RCO that is reviewing that principal's
     work.
5. **Precedence.**
   - The operator comes first: freeze, then an override, then the principals.
   - A principal's switch holds for its dwell against the other principal,
     except in `conserve`.
   - Contested switches go to the operator digest.
6. **No budget bypass exists** in this package.

**Not allowed for anyone but the operator's signature:** changing the
envelope or a ceiling, approving a profile, buying credits, enabling fast
mode, changing the fleet mode.

### 2.3 The operator command `wd-model` (alias `wd-malli`) (R1)

```
wd-model status                           # pools, lanes, overrides (read-only)
wd-model models                           # the §2.1 table (read-only)
wd-model set fable-5 planning --for 2h    # tier or exact profile, within the envelope
wd-model set all economy
wd-model reset fable-5 | all
wd-model freeze | unfreeze
```

**Tiers** map per lane to catalog profiles, so no model ids need to be
remembered:
- `economy` / `säästö`
- `standard` / `perus`
- `strong` / `vahva`
- `planning` / `suunnittelu`, a burst capped at 2 h

A refusal always prints the reason: the pool forecast, the envelope, the
freeze or the dwell.

### 2.4 One serialized executor (R8)

One external executor, run by the supervisor outside every lane, applies every
switch. It is the PR-3 executor with production ports.

**The intent.** Each switch intent is durable and carries:
- an idempotency key;
- lane, target profile, trigger and actor;
- the expected generation, PID and start time, and token identity;
- preconditions: checkpoint written, safe boundary, idle.

**Rules**
- Principals only submit intents; neither may bypass the executor.
- **Buddy fallback.** If the executor is unavailable, the other principal may
  invoke the **same** executor, and only after its exclusive ownership is
  proven. There is never a second execution path.
- **Unknown means HOLD.** An unknown owner, logon or integrity (F5 evidence)
  holds the switch. The executor never kills a process on an ambiguous
  identity.
- **Recovery** distinguishes queued, applied and verified. It resumes from the
  last durable state and never repeats an apply without its check.
- The process tree runs under a job object with its own timeout, because a
  scheduled task's limit does not reach the child.
- **Production ports** are not wired while Tools ownership is in CONFLICT.

### 2.5 Continuity state machine (F17)

A lane has three identity layers. A switch may change some of them:

| Layer | Examples |
|---|---|
| CLI conversation | transcript or thread id |
| Bridge identity | `agent_uuid`, `session_id`, `run_id`, owner token |
| Process incarnation | PID + start time |

| Path | Conversation | Bridge identity | Process | Continuity action |
|---|---|---|---|---|
| In-session switch (PR-12) | same | same | same | none; D3 verifies |
| Qualified resume relaunch | same | same (re-presented by the executor from the durable intent, only where the resume contract is qualified for that CLI version) | new | old process fenced first; claims and requests continue unchanged |
| Identity-changing relaunch | same or new | new | new | claims by fenced CAS; requests are reissued by their requesters, never transferred |

The fenced CAS works like this:
- The lane is quiescent.
- The old owner is verified gone by PID and start time.
- The executor moves the lane's own claims to the new owner token by a
  scoped CAS.
- The other principal verifies the move independently.

**States:**

```
REQUESTED -> QUIESCED -> FENCED -> APPLIED -> VERIFIED -> CONTINUED
    any failure -> HOLD -> rollback to the previous qualified explicit
                   profile and verified generation, or HOLD for Lead/operator
```

- `QUIESCED`: checkpoint written, at a safe boundary.
- `FENCED`: the old process is stopped, and that is verified.
- `VERIFIED`: D3 plus the identity check.
- `CONTINUED`: claims and requests handled per the path above.

**Invariants**
- **A frozen request binding is never mutated or auto-accepted by a
  successor.**
  - When the bridge identity changes, the executor posts an `identity_changed`
    notice to each requester of an open request.
  - The requester supersedes and reissues with a new `request_id`, digest and
    expected responder.
  - An unavailable requester leaves the work safely pending.
- **Never transferred:** RCO votes, operator signatures, veto clearance and
  privileges.
- **The handover record is evidence, not authority.** It never loosens nonce,
  digest or responder binding.
- Every transition is durably recorded under the intent's idempotency key.

**Mandatory details for the F15/F16/F17 interface contract** (Lead, round 3):
- An in-session switch never runs the relaunch-only stop and `FENCED` steps.
- `FENCED` covers every old claim-writing holder: child processes and
  heartbeat jobs, not only the parent PID.
- A partially completed claim migration is recoverable, and it is recovered
  before the lane makes any new task mutation.

### 2.6 Grok for every lane (R3)

- **Request kind.** Any lane may post `grok_consult` with: purpose (review,
  arbitration, research, brainstorm), effort, a prompt file of at most 24 KB,
  a deadline and a priority. This replaces the refusal in `bridge_workflow.py`.
- **Broker.** One serialized broker calls the existing helper.
  - The existing hourly guard stays the admission rule for autonomous
    calls, and every attempt counts. There are no refunds.
  - **Operator-requested calls are exempt from the hourly guard (R12).** The
    operator's direct words, session-observed in the lane that makes the
    call, are the only trigger; a peer relay is not. The call is recorded as
    `operator_requested` with the literal instruction, and it still counts
    in the ledger and the weekly-pool forecast. The exemption never buys
    top-ups or bypasses a weekly-pool limit error.
  - The result is a bound reply to the requester.
- **Unavailable.** A limit, auth, timeout or budget failure gives a reply of
  `skipped` with the reason class. The requester carries on. **Grok is never a
  gate.**
- **Measurement (F4).**
  - JSON output: model, effort, session, tokens, error class.
  - One ledger that includes calibration and manual runs.
  - The timeout scales with effort.
- **Budget.** Apart from the operator-requested exemption above, loosening
  the hourly guard for autonomous calls needs separately signed, measured
  admission rules. That is not in this package.

### 2.7 Cheap routine work, strong planning (R4, R9)

**Task classes** are bridge facts, never free text:

| Class | Default direction (only known qualified signed profiles) |
|---|---|
| `routine` (bookkeeping, replies, polling, parsing, mechanical edits) | economy: Haiku or Sonnet via subagent; Codex Luna |
| `implementation` | standard |
| `review` (RCO) | strong, never below the reviewer default |
| `planning` (design, brainstorm, research, arbitration) | strong; premium only as a budget-checked burst; Grok via `grok_consult` as the third family |
| `incident` | burst rules |

**Brief-then-delegate:**
1. The strong model writes the brief: goal, files and lines, rules and
   invariants, pitfalls, acceptance tests, the definition of done, and what
   not to touch. That is the best initialization.
2. The route is the cheapest known qualified profile whose measured success
   rate for the class meets the threshold.
3. A cheap executor, with no bridge authority, works in an isolated worktree
   or subagent.
4. The strong model, or the tests, verifies the result.
5. On failure, escalate one tier at a time; after two escalations the
   principal does the task itself.

A routing ledger (class, profile, attempts, success, tokens, pool cost) feeds
the success rates. With no measurement, the default table above applies, and
only with signed profiles. (a)-class code still gets exact-head dual-RCO
review, whoever wrote it.

**A rule conflict to resolve.** `CLAUDE.md` Rule 8 makes the strongest model
the default for every session and every subagent that is not explicitly fixed.
Making `routine` and `implementation` default cheaper needs an amendment to
Rule 8. That amendment is (a)-class and needs the operator's explicit
signature; it is part of the §4 packet.

### 2.8 Wake delivery and contracts (from Lead's audit)

- **One outstanding wake per lane.**
  - A durable watermark plus a dirty flag, and a typed notification with a
    pinned drain helper.
  - Late replies, cancellations and vetoes are never coalesced.
  - Only notifications are coalesced, never canonical events.
  - No-op drains are invisible to the operator.
- **When delivery is uncertain:** reconcile with bounded, idempotent
  recovery, never a blind resubmit. There is a backlog migration, and a
  rollback to the relay.
- **One versioned bootstrap and role contract,** its hash verified at session
  start. The old layers become historical, and CI lints the active prompt set.

### 2.9 Queue and guard correctness

| Item | Design |
|---|---|
| Scope race | `WorkQueueV1` created through the #1751 same-logon mutex helpers. Claim, release, heartbeat and applied sweep run inside it. A timeout gives `work_queue_busy`; nothing proceeds unlocked. Bridge events are written after the lock is released. |
| Git guard | Parse leading options. `-C` is guarded against its target. `--git-dir`, `--work-tree`, `--namespace` and `-c core.*`/`include.*` are refused on branch moves, as are `GIT_CONFIG_*`. Unknown options fail closed. |
| Lease | The heartbeat follows the long-lived worker. The old owner is fenced when stale. Leases retire per task, so a live process never renews a finished task forever. The claim cwd is normalized to an absolute path. |
| Reply UX | A pinned `Reply-ToRequest -RequestId` fetches and binds the request itself; the requester can supersede its own request. |
| Head check | The writer rejects a non-40-hex head on statuses that require a commit (for example `rco_pass`, `build_consensus_pass`), not on every decision. |
| Claim UX | A discoverable claim schema, a preflight (`-Explain`) and examples. There is no permissive fallback. |
| Suspected items | Reproduce the wrapper-attribution issue and the reserved-operator refusal in isolation, with the registry and profile both present and missing, and in both writers. Fix only what reproduces. Never probe the live bridge with a spoof. |

### 2.10 The composer rule for brainstorms and sprint plans (R11)

**Rule.** The final synthesis of every brainstorm and every sprint plan is
written by the **composer**: the available model and effort with the highest
external intelligence index in the registry. Other lanes and models contribute
inputs, objections and rounds; the composer writes the document of record.

**Source of the ranking**
- The registry's recorded external index (today the Artificial Analysis
  Intelligence Index, v4.3.2 in `configs/model_registry.json`, updated
  2026-09-27), with its version, provenance and freshness. It is refreshed
  by the registry refresh (F3), never by hand in a session.
- Only profiles in the signed envelope count (F21 receipt, known quota cost).
- **Ties** (equal index, or overlapping uncertainty where the source gives
  it): the higher coding index wins, then the lower quota cost.
- A stale index (older than the registry's freshness bound) or a missing one
  means the rule reports `composer_unknown` and the principal names its
  choice and reason in the document. There is no silent guess.
- Today (MEASURED from the registry): `claude-opus-5-5` at `max` effort, index
  58; then `claude-opus-5-5` `xhigh` 56; `claude-fable-5-1` `max` and
  `gpt-6-astra` `max` 53.

**How the composer runs**
- A principal whose own profile is the composer writes the synthesis itself.
- Otherwise it composes through a subagent or a headless call on the composer
  profile (brief-then-delegate in reverse: the inputs are the brief), or asks
  the executor for a `planning` burst switch (§2.4, §2.7).
- Grok composes only if it ranks top AND the call goes through `grok_consult`
  with the hourly guard; it is never switched to automatically (§2.6).

**When the composer is unavailable** (quota exhausted, pool in conserve, or
provider down)
- Wait up to a stated deadline, then use the next-highest available profile.
- The document records `composer_fallback` with the reason, and the profile
  that composed it.
- The budget and premium-burst caps still apply. The rule never bypasses a
  budget or the hourly Grok guard.

**What the rule is and is not**
- It chooses who *writes* the synthesis. It is not an approval, a review
  or a gate; RCO review and the signature rules are unchanged.
- It reconciles Lead's round-1 point that a benchmark is only task-specific
  evidence: the external index is used for this one role, with provenance,
  because the operator directed it. It does not rank models for other task
  classes; those still use F21 measurements.
- Every brainstorm and sprint-plan document records its composer profile,
  the index value and version used, and any fallback.

## 3. Acceptance table

**Owners:** L = Lead, T = Tools, F = fable-5. These are planning proposals,
not assignments.

**How the build is organized**
- One owner per physical file. Where F11, F12 and F17 touch the writer, one
  of them owns the file and the others go through it. F13 and F16 share one
  launch contract with one owner.
- The F15 → F16 → F17 interface contract is written and reviewed first; the
  parallel edits start after it.

**What every row needs**
- (a)-class rows need an exact-head dual-RCO review and independent tests.
  Tools never solely validates its own broker or registry.
- Every row runs under kernel-name and runtime-root isolation.

**Activation.** "Activation" means the authorized deployment and flag
boundary; merging alone never mutates production.

| # | Stage | Feature | Owner | Evidence to accept | Fail-closed | Activation | Rollback |
|---|---|---|---|---|---|---|---|
| F1 | 1 | Wake telemetry (watermarks, reasons, no-op ratio, latency) | L | isolated trace reproduces the numbers | unknown shown as unknown | deploy (read-only) | remove the reader |
| F2 | 1 | One versioned bootstrap contract + prompt lint | L | fresh and resumed sessions report the same hash | wrong or missing hash means fail closed | deploy | previous contract |
| F3 | 1 | Registry v2 + stored pool cost + collector provenance | T | meter matches operator pool readings on 7 days of data, within a stated tolerance | unknown stays unknown | deploy | data revert |
| F4 | 1 | Grok measurement and ledger | T | every call in the window is in the ledger | unclassified error means cooldown | deploy | helper pin |
| F5 | 1 | Lock-participant evidence | T | runs before activation and after reboot | more than one logon or integrity means HOLD | gate for Stage 5 | n/a |
| F6 | 1 | Read-only dashboard | T | matches canonical revisions on a replay | unknown shown as unknown | deploy | n/a |
| F21 | 1 | Minimal qualification | F writes, T runs, RCOs evaluate | per profile: task-class coverage, repeats, sample size and uncertainty, provider, version and effort, provenance, freshness; adversarial holdout cases. Receipts are bound to the exact code, profile, provider version and freshness, and never claim universal intelligence or uninterrupted availability | no receipt means out of the envelope | before the catalog signature | n/a |
| F7 | 2 | One outstanding wake per lane | L | Lead's audit acceptance list on an isolated runtime, plus backlog migration | uncertain means reconcile, never resubmit blindly | canary: Tools only with verified ownership and no critical work; then Lead; then one Claude lane; then the rest one at a time | flag back to relay |
| F8 | 3 | Work-queue serialization (replaces #1567) | F | cross-runtime race harness: one winner, no resurrection | busy means refuse | deploy | revert |
| F9 | 3 | Git-guard option parsing | F | probe matrix with success twins | unknown option means refuse | deploy | revert |
| F10 | 3 | Worker-bound lease, fencing, retirement, absolute cwd | F | a long task keeps its claim; a killed owner loses it; a finished task retires | unknown owner means no renewal | deploy | revert |
| F11 | 3 | `Reply-ToRequest` + requester supersede | F | binding equals `-ReplyToEventJson` on the corpus | ambiguous id means refuse | deploy | revert |
| F12 | 3 | Commit-status head validation | F | negative tests | reject | deploy | revert |
| F22 | 3 | Claim schema discoverability | F | examples and `-Explain` cover every resource kind | unknown kind means refuse (unchanged) | deploy | revert |
| F23 | 3 | Isolated reproduction of the suspected items | T | reproduction or a documented non-repro | n/a | n/a | n/a |
| F13 | 4 | Explicit per-lane launch + preflight enforce | L | preflight equals D3 on every lane | mismatch means refuse | per-lane canary | previous qualified explicit profile and verified generation, else HOLD |
| F15 | 5 | Switching policy (pure) | F | property tests of every §2.2 guardrail; shadow coverage of every enabled pool and actuator path, plus negative cases: stale or missing quota, boundary caps, conflicting principals, frozen override, reviewer protection, clock and expiry, crash and replay | unknown means HOLD | shadow until coverage holds | flag off |
| F16 | 5 | Serialized executor (§2.4) | L | crash at each phase recovers without a double side effect; F5 green | ambiguous means HOLD, never kill | canary fable-5 (Lead observes, both RCOs available), then Tools and Lead, then each RCO when not reviewing affected work | flag off |
| F17 | 5 | Continuity state machine (§2.5) | F | a relaunch mid-task keeps its claims per path; requests are reissued, never transferred; a forged handover is rejected | no record means no successor | with F16 | revert |
| F18 | 5 | `wd-model` | F | end-to-end on the canary | refusal prints the reason | with F16 | n/a |
| F19 | 5 | Task classes + brief-then-delegate + ledger | F | ledger shows cost per class; quality holds on F21 tasks | no measurement means signed defaults only | shadow ledger first | prompt table off |
| F24 | 5 | Composer rule (§2.10) | F | the chosen composer equals the registry top on fixtures (ties, stale, missing, unavailable); every brainstorm and sprint-plan document records its composer | unknown or stale index means `composer_unknown`, recorded; unavailable means a recorded `composer_fallback` | with F19; the rule text also goes into the F2 bootstrap contract | prompt rule off |
| F20 | 5 | `grok_consult` + broker | T implements; F and RCOs test | none lost; the hourly guard is never exceeded by an autonomous call; an operator-requested call passes only with a session-observed, recorded instruction, and a relayed one is refused | unavailable means `skipped` | with Stage 5 | flag off |
| F25 | 1 | Shared quota visibility (§2.1) | T | every lane's boot brief and `wd-model status` show every pool with age and source; matches the F3 meter on a replay | unknown shown as unknown; no work routed to an unknown pool except urgent, with the reason | deploy (read-only) | remove the reader |

**Closures**, each only after a diff and test mapping, never by title alone:
- #1567, superseded by F8;
- #1638, perhaps superseded by F7;
- #1656, folded into F3 or closed;
- the #1751 slices, after main CI.

## 4. Proposed signature scope (a proposal, not an authorization)

**One signature is the target.** It depends on a complete frozen packet
satisfying every gate; if the packet is incomplete, the work does not proceed
on a partial signature.

**The packet binds:**
- head, tree, base, tag and release-notes SHA256;
- the SHA256 of:
  - the `CLAUDE.md` Rule 8 amendment;
  - the signed catalog (profiles with F21 receipts, the principals, the
    envelope);
  - the Grok routing policy;
  - the composer rule (§2.10);
  - the activation plan.

**The activation plan lists for every stage:**
- the flag;
- the coverage predicate (no elapsed-time-only acceptance);
- the canary order;
- stop conditions;
- rollback;
- a maximum authorization of 14 days, counted from the defined activation
  authorization time and never reset by a restart. On expiry:
  - a stage that was never activated stays off;
  - an enabled automation stops accepting new intents, and its in-flight work
    either settles safely or holds;
  - expiry and freeze never blindly kill lanes or undo durable completed
    effects;
  - reapproval is needed.

Freeze and the kill switch take precedence over every stage.

**Explicitly excluded:** Stage-2 cutover, Rule 9b activation, approval
carry-forward, any budget bypass, and fast or credit tiers. Merge authority
stays under Rules 9a and 9b. This packet does not change them.

**Steps:**
1. #1751 rollout.
2. The interface contract (F15/F16/F17), then file-disjoint slices in stage
   order, reviewed as they land.
3. Freeze; one isolated matrix; CI; dual RCO at the exact head; build
   consensus.
4. One signature.
5. Merge, then staged activation against the predicates.

## 5. Deferred and known limitations (stated openly, not "all solved")

- **B7 break-glass** for a hung live owner. The continuity work covers the
  relaunch case only.
- **S10:** an identical unbound replay can reopen a request.
- **PS/Python payload casing parity.** Current binding semantics are kept.
- **Approval carry-forward** for content-identical rebases.
- **Rule 9b** and **Stage-2.**
- **The full PR-10, PR-14 and PR-16 frameworks, and the app-server
  transport.**
- **Other Global mutexes** (reboot control, supervisor reconcile),
  cross-logon access and low integrity. These are measured limitations, with
  evidence from F5.
- **Tokens are not cryptographic authentication.** Session-observed operator
  input is observed, not authenticated.
- **196 historical rows** fail the strict schema; they are not rewritten.
- **Branch protection** on `main`: an operator setting, and there is none
  today.

## 6. Invariants

- Unknown means HOLD. There is no assumed downgrade and no raise.
- Grok is never a gate, and never part of automatic model switching until its
  pool is measurable.
- No automatic purchase, no fast tier, no budget bypass. The only exemption
  is operator-requested Grok calls from the hourly guard (§2.6); the weekly
  pool still applies.
- Every lane can see every pool's quota state; seeing grants no authority.
- The executor is the only actuator, and a frozen request binding is never
  mutated.
- Every switch is an event with its inputs, verified by D3, with a rollback.
- Exact-head dual-RCO review for (a)-class work. Grok review is advisory.
- Brainstorm and sprint-plan syntheses are written by the composer (§2.10),
  and each records its composer profile, index version and any fallback.

## 7. Brainstorm record

**Round 1** (fable-5 04:34:53Z; Lead 04:36:20Z). Lead's position:
- one acceptance packet with staged engineering;
- no elapsed-time-only activation;
- no refunds;
- one executor;
- quota cost, not API price;
- unknown means HOLD;
- no budget bypass;
- defer carry-forward, break-glass and casing;
- minimal qualification is mandatory.

All of it was adopted.

**Round 2** (fable-5 04:38:23Z; Lead 04:40:00Z). Continuity is included, but
without automatic successor acceptance: requesters reissue. The following
were accepted with these conditions:
- the operator's lane-window channel, for set, reset and freeze;
- F21 measuring quality and quota cost, with an evidence standard and a
  smaller measured envelope;
- the owners, as planning only, with one owner per file and the interface
  contract first;
- coverage of every pool and path plus negative cases, rather than counters;
- the 14-day expiry, from authorization time;
- the canary orders in §3.

The suspected items are labelled as such, and the closures need a diff
mapping.

**Round 3** (fable-5 04:42:51Z; Lead 04:44:02Z). **Confirmed** ecb3a643 as the
joint DESIGN proposal, with no objections. It is not implementation approval,
an RCO_PASS, build consensus, a signature, production authority, or
verification of every measured claim. Lead's three details were added: the
contract details in §2.5, the expiry semantics in §4, and the receipt binding
in F21.

Joint recommendation (Lead, verbatim):

> Suosittelemme yhtä tarkasti rajattua Bridge v2 -hyväksyntäpakettia: ensin1751
> valmiiksi, sitten herätteiden hallinta ja näkyvyys, jono- ja turvakorjaukset
> sekä mitattuun kustannus- ja laatutietoon perustuva mallinvaihto; yksi
> allekirjoitus on tavoite, käyttöönotto tapahtuu tarkistetuissa vaiheissa
> ilman porttien ohituksia.

Implementation specifications, test evidence and the frozen signature packet
remain separate gates.

**Addendum after round 3** (fable-5, 2026-09-28). The operator's fifth
directive (R11) added the composer rule (§2.10, F24). It was composed by
fable-5 on `claude-opus-5-5`, the current registry top, and sent to Lead for
confirmation or objection; it is not yet part of the confirmed joint
proposal until Lead answers.
