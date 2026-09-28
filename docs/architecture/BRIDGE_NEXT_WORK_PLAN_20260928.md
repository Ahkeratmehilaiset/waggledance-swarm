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

> Eli tämän kokonaisuuden tarkoitus on se ettei parvi ajaudu kiintiö
> limitteihin ja se voidaan ennakoida siten että jokaisella parvewn jäsenellä
> on oikeus säätää toistensa mallia ja efforttia ennakoivana kunnossa pitona
> ettei toiminta lakka

> esim. seikkaperäiset sprint planit tehdään tehokkaalla mallilla ja sitten
> kun seikkaperäinen suunitelma on valmis palaudutaan tilanteen vaatimaan
> käyttörajat ylläpitävään malliin ja toteutetaan

> eli kaikki tämä pitää pystyä tapahtumaan automaattisesti ilman operaattorin
> väliintuloa -parvi pysyy toiminta kykyisenä, ajantasalla haastavissa
> tilanteissa, se voi myös oppia

> se voi myös oppia kuten waggle dance

> onko tässä nyt huomioitu se, että joskus suorittava malli kaatuu tai
> käyttöraja umpeutuu ja sen työtä jatkamaan pitää pystyä inkarnoimaan
> tuuraaja

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
| R13 | **The purpose of the whole package:** the swarm never runs into quota limits. Limits are forecast, and every swarm member may adjust any other member's model and effort as predictive maintenance, so that work never stops | §2.2 |
| R14 | Plan, then execute: detailed sprint plans are made on a strong model; when the plan is done, the lane returns to the quota-preserving model the situation calls for, and the plan is implemented on that | §2.7 |
| R15 | All of this happens automatically, without operator intervention: the swarm stays operational and up to date in hard situations, and it learns, like the waggle dance | §2.11 |
| R16 | When a working model crashes or its usage limit runs out mid-task, a stand-in can be incarnated to continue its work | §2.12 |

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
- READ (code, 2026-09-28): `Write-AgentEvent.ps1` never calls
  `Assert-AgentBridgeSessionIdentity`, so any caller can write an event as
  `agent=operator` or `system`; the reserved-label refusal exists only in the
  claim, release, heartbeat and session scripts. The earlier SUSPECTED
  wording (lines 437-499) was wrong about the location. See
  `BRIDGE_V2_IMPLEMENTATION_MAP_20260928.md` §1 C1.
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

**What counts as known** (Grok and Lead, final round). A cost, quota or
quality value is known only with validated provenance, stated uncertainty, a
TTL and, for quality, qualification. The kinds of evidence are kept apart:
observed provider quota and cost; synthetic or replayed workload results
(F21, valid for admission); and shadow forecasts, which never drive an
actuation on their own. A value past its TTL or a `None` pool is unknown. An
external score's freshness is the source's own measurement date, never the
time the registry was written.

**`wd-model models [--json]`** gives one row per catalog model and effort,
Grok and Haiku included, with:
- those fields;
- pool state (used, reset, forecast);
- CLI availability;
- membership of the signed envelope, as a column (a model outside the
  envelope still has its row).

Unknown values are shown as unknown cells. This table is the principals' decision
input, and a compact copy goes into their boot brief.

**Shared quota visibility (R12).** Every lane, not only the principals, can
read every pool's state: each Claude and Codex pool, and Grok's weekly pool
as far as it is measurable (F4 ledger tokens, calibrated against the
operator's readings of the account page).
- It is a read-only snapshot (`wd-model status --json`, and a compact line in
  each lane's boot brief and wake notification), with its age and source.
- A lane checks a peer's pool before it sends that peer work or a
  `grok_consult`; a peer in `conserve` or with an unknown pool gets only
  urgent work, and the sender says why. Urgency never authorizes an
  unqualified dispatch.
- Reading another lane's quota grants no authority over it; switching stays
  with the executor (§2.4).

### 2.2 Who may switch, and within what (R1, R6, R13)

**The envelope.** The operator-signed set of profiles that automatic or
member switching may use.
- A profile enters **automatically** when its F21 receipts (quality and
  quota cost) meet the signed admission thresholds (§2.11). The operator
  signs the thresholds and ceilings once, not each profile (R15).
- A profile whose cost cannot be measured stays out. A smaller, measured
  envelope is preferred to invented values.
- Profiles not yet in the envelope are used only by the qualification
  harness, under the signed measurement budget (§2.11), never for real
  work.
- Fast, priority and pay-per-use credit tiers are never in it.
- Grok is outside automatic model switching, because its pool cannot be read.

| Channel | May do |
|---|---|
| Operator's own terminal (`wd-model`) | `set`/`reset` within the envelope with expiry; `freeze`/`unfreeze` |
| Operator's direct words in a lane's own window (session-observed; the originating session and the literal instruction are recorded; never claimed as cryptographic authentication) | `set`/`reset` within the envelope with expiry; `freeze`. `unfreeze` only from the operator's own terminal or direct input |
| Every swarm member (`codex-lead-1`, `codex-tools-1`, `fable-5`, `claude-rco-1`, `claude-rco-2`), triggered by a request, a written assessment, a measurement or a quota forecast (R13) | submit a bounded **switch intent** for any lane, including itself, to the executor (§2.4) |
| Principals (`codex-lead-1`, `fable-5`) | the same, plus precedence over other members' intents and first handling of contested switches |
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
   - Hysteresis for raises. The tick length and hysteresis margin are signed
     parameters.
   - The executor classifies intents itself and ignores caller labels: a
     *revert* is an intent whose target is the lane's last verified
     in-envelope profile; *conserve* applies only when the projection has
     crossed a trip line. A revert skips the dwell only; it still counts
     toward the hourly cap and the pool tick.
4. **Reviewer independence.**
   - An RCO sets its own effort by an effort-only intent within its signed
     range.
   - Nobody lowers an RCO below the reviewer default.
   - No member changes an RCO that is reviewing that member's work. This
     keeps an author from weakening its own reviewer.
   - For quota reasons an RCO may be moved to another pool or family at
     equal or higher reviewer quality, never lowered.
5. **Precedence.**
   - The operator comes first: freeze, then an override, then the
     principals, then the other members.
   - A switch holds for its dwell against an equal-or-lower-precedence
     member, except in `conserve`.
   - A contest is resolved deterministically, with no operator step (R15):
     the incumbent profile stays and the competing intent is not applied; a
     higher-precedence member may replace it after the dwell. The operator
     digest reports contests but never has to act.
6. **No budget bypass exists** in this package.

**Not allowed for anyone but the operator's signature:** changing the
envelope rules or a ceiling, approving a profile outside the signed
admission thresholds, buying credits, enabling fast
mode, changing the fleet mode, changing the admission thresholds, the
measurement budget or the learning bounds (§2.11). The operator may still
drop an RCO below its floor from the operator's own terminal, with an expiry.

**Predictive maintenance: never run into a limit (R13).** This is the goal
the rest of the package serves.
- **Forecast.** From the shared quota view (§2.1, F3 meter), each pool gets a
  forecast of use until its reset. A pool whose forecast crosses its trip
  line before the reset is *at risk*, early enough to act (the lead time is
  a signed parameter).
- **Who acts** (proposed new authority, §4). Any member that sees an at-risk
  pool submits intents for the lanes on that pool, its own or others'. The executor deduplicates intents
  per pool and tick, so several members seeing the same risk cause one
  action.
- **Order of measures**, cheapest to the work first:
  1. route delegated and routine work to a pool with headroom (§2.7);
  2. lower the effort of non-reviewing lanes on the at-risk pool;
  3. move lanes to a qualified profile on another pool or family;
  4. lower the tier of non-reviewing lanes, within task-class floors;
  5. only as the last step, defer non-urgent work; review and incident work
     keep running.
- **Restore.** When the forecast recovers, the same members raise the lanes
  back under the same rate rule (a revert skips the dwell only). An urgent
  rollback that does not fit that rule HOLDs; it never bypasses it.
- **Atomic pool admission.** Admission is computed atomically per shared
  pool, reserving the bounded expected use of simultaneous lane and delegated
  calls. Lower effort is not assumed to mean lower measured cost.
- **Limits of the rule.** Every intent passes the guardrails above: unknown
  quota means HOLD, not a guess; reviewer floors hold; there is no purchase
  or budget bypass. A member that cannot act (for example because the
  executor is down) posts the at-risk forecast to the principals and
  informs the operator.
- **Measure of success** (an objective with measured evidence, not a
  guarantee): no pool reaches its limit while a qualified alternative
  existed, and no lane stops for quota reasons.

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

- One shared tier table (class, maximum duration, trip-line behaviour) serves
  `wd-model`, the executor and §2.2; `premium` is in it as a burst-only tier.
- A `set` without `--for` uses the signed default expiry, which is printed.
  A duration above the tier's cap is refused, not clamped.
- `wd-model` only enqueues intents to the executor (§2.4); it has no apply
  path of its own. `unfreeze` is refused when the caller is a lane process.

A refusal always prints the reason: the pool forecast, the envelope, the
freeze or the dwell.

### 2.4 One serialized executor (R8)

One external executor, run by the supervisor outside every lane, applies every
switch. It is the PR-3 executor with production ports.

**The intent.** Each switch intent is durable and carries:
- an idempotency key;
- lane, target profile, trigger and actor;
- the expected generation, PID and start time, and token identity;
- preconditions: for a relaunch, checkpoint written, safe boundary and
  idle; for an in-session switch, a safe boundary only. A bounded wait, then
  an operational wait with a visible reason and a bounded retry (§2.11). A generation,
  PID or start-time mismatch means the intent is not applied, and nothing is
  killed.

**Rules**
- Members only submit intents; no member may bypass the executor.
- **Buddy fallback.** If the executor is unavailable, a principal may
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
| Qualified resume relaunch | same | same (re-presented by the executor from the durable intent, only where the resume contract is qualified for that CLI version; an unqualified resume is never attempted, and the identity-changing path is used instead) | new | old process fenced first; claims and requests continue unchanged |
| Identity-changing relaunch | same or new | new | new | claims by fenced CAS; requests are reissued by their requesters, never transferred |

The fenced CAS works like this:
- The lane is quiescent.
- The old owner is verified gone by PID and start time.
- The executor moves the lane's own claims to the new owner token by a
  scoped CAS.
- The executor checks the result deterministically (the claim state equals
  the intended CAS result). The other principal's independent verification
  is also required for the first live canary; after that it is an audit.

**States:**

```
relaunch:    REQUESTED -> QUIESCED -> FENCED -> APPLIED -> VERIFIED -> CONTINUED
in-session:  REQUESTED -> APPLIED -> VERIFIED -> CONTINUED
any failure -> HOLD -> rollback: the previous profile if it is still in the
               envelope with a verified generation, else the lane's signed
               safe default profile; never `native`
```

Retries are finite per intent: backoff, a total time and attempt budget,
and a circuit breaker. If the rollback cannot start within that budget, or
the identity stays ambiguous, the lane stays in HOLD (a safety HOLD, §2.11)
and the principals and the operator are notified; there is no endless
relaunch.

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
  - **Operator-requested calls are exempt from the hourly guard (R12;
    proposed new authority, §4).** The operator's direct words,
    session-observed in the lane that makes the call, are the only trigger;
    a peer relay is not. Each exemption is a scoped, single-use instruction
    record with replay prevention, the prompt or request digest and a
    deadline; one ask never creates an unlimited exemption. The call is
    recorded as `operator_requested`, and it still counts in the ledger and
    the weekly-pool forecast. It never buys top-ups or bypasses a
    weekly-pool limit error. Until that policy is signed and active, the
    deployed rules stay as they are.
  - **Admission** among waiting requests: lane-class priority from a signed
    table, then age; a self-raised priority is ignored. Effort and timeout
    come from a signed cap table. A request not admitted gets an immediate
    `rate_limited` skip.
  - Composer syntheses (§2.10) may use at most a signed share of the hourly
    admissions, so the other lanes keep their R3 access.
  - The result is a reply bound to the request id and the prompt digest.
  - A missing error class is `unknown`, never success; the helper's stderr
    is kept in the ledger.
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

### 2.7 Cheap routine work, strong planning (R4, R9, R14)

**Task classes** are bridge facts, never free text:

| Class | Default direction (only known qualified signed profiles) |
|---|---|
| `routine` (bookkeeping, replies, polling, parsing, mechanical edits) | economy: Haiku or Sonnet via subagent; Codex Luna |
| `implementation` | standard |
| `review` (RCO) | strong, never below the reviewer default |
| `planning_synthesis` (the document of record of a brainstorm or sprint plan) | the composer (§2.10) |
| `planning` (design, research, arbitration, brainstorm rounds) | strong, ranked by expected cost; premium only as a budget-checked burst; Grok via `grok_consult` as the third family |
| `incident` | burst rules |

**Brief-then-delegate:**
1. The strong model writes the brief: goal, files and lines, rules and
   invariants, pitfalls, acceptance tests, the definition of done, and what
   not to touch. That is the best initialization.
2. The route is the profile with the lowest *expected* pool cost (pool cost
   × expected attempts), pools in `conserve` excluded, whose receipt is
   fresh and meets the signed minimum sample, maximum uncertainty and
   success threshold. A stale receipt counts as none.
3. A cheap executor, with no bridge authority, works in an isolated worktree
   or subagent.
4. Both the tests and the strong model's check against the brief verify
   the result.
5. On failure, escalate one tier at a time; after two escalations the
   principal does the task itself.

**The plan-then-execute cycle (R14).** Sprint work runs in three steps:
1. **Plan.** The lane gets a `planning` burst on the composer profile
   (§2.10) and writes a *detailed* sprint plan: for every step, the brief
   above (goal, files and lines, invariants, pitfalls, acceptance tests,
   definition of done, what not to touch), so that a cheaper model can
   carry it out.
2. **Return.** When the plan is done, meaning committed and recorded with its
   digest, the lane requests the return at the next safe boundary; the
   return never forces a transition the rate or dwell rules forbid. The lane returns to the
   profile the *current* quota forecast calls for (§2.2, R13), which is not
   necessarily the profile it had before. The 2 h burst cap remains a
   backstop, not the normal exit.
3. **Execute.** The steps are implemented on that quota-preserving profile,
   by the lane itself or by delegation. Every finished step is committed and
   its status recorded in the plan's step journal, so every step boundary is
   a durable checkpoint a stand-in can continue from (§2.12). A failed step escalates one tier at
   a time, as above; re-planning goes back to phase 1 only when the plan
   itself is wrong, not when one step fails.

A routing ledger (class, profile, attempts, success, tokens, pool cost) feeds
the success rates. With no measurement, the default table above applies, and
only with signed profiles. (a)-class code still gets exact-head dual-RCO
review, whoever wrote it.
- If two members assign different classes to one task, the higher class wins
  deterministically (review > incident > planning_synthesis > planning >
  implementation > routine).
- If the task-class table is switched off, the pre-amendment Rule 8 text
  applies, so rollback always has a default.

**A rule conflict to resolve.** `CLAUDE.md` Rule 8 makes the strongest model
the default for every session and every subagent that is not explicitly fixed.
Making `routine` and `implementation` default cheaper needs an amendment to
Rule 8. That amendment is (a)-class and needs the operator's explicit
signature; it is part of the §4 packet.

### 2.8 Wake delivery and contracts (from Lead's audit)

- **One outstanding wake per lane.**
  - A durable watermark plus a dirty flag, and a typed notification with a
    pinned drain helper.
  - Late replies, cancellations and vetoes are never coalesced: they
    preempt, and are delivered apart from the coalesced notification slot.
  - Only notifications are coalesced, never canonical events. The drain's
    authority is the watermark over the canonical log, not the notification
    payload.
  - A supersede creates a new request id and a non-coalesced cancel of the
    old one; the frozen binding is never edited.
  - No-op drains are invisible to the operator.
- **When delivery is uncertain:** reconcile with bounded, idempotent
  recovery, never a blind resubmit. There is a backlog migration, and a
  rollback to the relay.
- **One versioned bootstrap and role contract,** its hash verified at session
  start. It is canaried on one lane first, and the previous hash stays valid
  for sessions that have not yet moved to the new one. The old layers become historical, and CI lints the active prompt set.

### 2.9 Queue and guard correctness

| Item | Design |
|---|---|
| Scope race | A **new** `WorkQueueV1` mutex (none exists today; the Python work queue takes no lock at all), created through the #1751 same-logon mutex helpers. Claim, release, heartbeat and applied sweep run inside it. A timeout gives `work_queue_busy`; nothing proceeds unlocked. Each mutation writes an idempotency key and an outbox record in the same critical section; bridge events are published from the outbox after the lock is released, so a crash between the two loses no event, and a replay of a bound key returns the original result. |
| Git guard | Parse leading options. `-C` is guarded against its target. `--git-dir`, `--work-tree` and `--namespace` are refused on branch moves. Only an allowlist of safe `-c` keys passes on a branch move; every other `-c`, and the `GIT_CONFIG_*`, `GIT_DIR`, `GIT_WORK_TREE` and `GIT_NAMESPACE` environment variables, are refused. Unknown options fail closed. |
| Lease | The heartbeat follows the long-lived worker. The old owner is fenced when stale. Leases retire per task, so a live process never renews a finished task forever. The claim cwd is normalized to an absolute path. The heartbeat proves only that the process lives: a claim with no progress proof beyond a signed bound is marked `wedged`, visibly; the claim stays held, and other file-disjoint eligible work continues (R15). Automatic takeover of a wedged *live* owner stays deferred (B7, §5). |
| Reply UX | A pinned `Reply-ToRequest -RequestId` fetches and binds the request itself; the requester can supersede its own request. |
| Head check | The writer rejects a non-40-hex head on statuses that require a commit (for example `rco_pass`, `build_consensus_pass`), not on every decision. |
| Claim UX | A discoverable claim schema, a preflight (`-Explain`) and examples. There is no permissive fallback. |
| Suspected items | Reproduce the wrapper-attribution issue and the reserved-operator refusal in isolation, with the registry and profile both present and missing, and in both writers. Fix only what reproduces. Never probe the live bridge with a spoof. |

### 2.10 The composer rule for brainstorms and sprint plans (R11)

**Rule.** The final synthesis of every brainstorm and every sprint plan is
written by the **composer**: among the *eligible* profiles, the one with the
highest external intelligence index. Other lanes and models contribute
inputs, objections and rounds; the composer writes the document of record.

**Step 1: eligibility first** (Lead, final round). A profile is eligible only
if all of these hold:
- it is a signed profile in the envelope, with F21 quality and quota-cost
  evidence;
- the budget projection allows it (the §2.2 trip lines and premium-burst
  cap);
- it is available now (pool known, not exhausted or in `conserve`,
  provider up); a missing or stale quota makes it ineligible, never a
  fallback;
- its effort is allowed for the `planning` class.

**Step 2: ranking** among eligible profiles only.
- Only comparable scores count: the same named index and version (today the
  Artificial Analysis Intelligence Index v4.3.2 in `configs/model_registry.json`,
  updated 2026-09-27). Provider model ids and effort settings are bound
  exactly.
- The ranking reads one **frozen registry snapshot**, recorded by digest per
  synthesis, so a registry refresh cannot change the winner mid-task. The
  registry is refreshed only by F3, never by hand in a session.
- **Ties:** scores within a signed epsilon, or with overlapping uncertainty
  where the source gives it, are ties, so a sub-point bump cannot force a
  costlier effort. Ties go to the higher coding index, then the lower quota
  cost, then the signed profile id, which makes the order total. Missing tie
  data never picks a winner; the next key decides.
- **Staying up to date (R15).** A registry refresh that changes the winner
  applies automatically to the next synthesis, but only if the new score
  carries the source's own measurement date and stays within a signed
  plausibility bound against the previous version; otherwise the result is
  `composer_unknown` until the next refresh.
- The registry values are a local snapshot of an external source, not an
  independent verification by the swarm.
- In today's snapshot the top entries are `claude-opus-5-5` `max` (58),
  `claude-opus-5-5` `xhigh` (56), and `claude-fable-5-1` `max` and
  `gpt-6-astra` `max` (53). Which of them is eligible depends on step 1.

**When the ranking is unknown** (stale, missing or incomparable scores)
- The result is `composer_unknown`. A principal may then write a clearly
  labelled **provisional synthesis** with a profile that still passes every
  step-1 condition. It is never presented as satisfying the highest-index
  rule.
- No eligible profile at all means HOLD.

**When the top profile is unavailable**
- Wait up to a stated deadline with a bounded retry count, with no costly
  polling loop; a freeze stops the wait.
- Then use the next eligible profile in the ranking, within the strong
  tiers, and record `composer_fallback` with the reason. The wait is a signed
  duration and never holds the executor lease.
- A fallback never bypasses any step-1 condition.

**How the composer runs**
- A principal whose own profile is the composer writes the synthesis itself.
- Otherwise it composes through a subagent or a headless call on the composer
  profile (the inputs are the brief), or asks the executor for a `planning`
  burst switch (§2.4, §2.7).
- **Subagent and headless composition pass the same admission controls as a
  lane switch** (envelope, budget, burst cap). Otherwise delegation would be a
  budget or envelope bypass.
- Grok composes only if it ranks top among eligible profiles AND the call goes
  through `grok_consult`; it is never switched to automatically (§2.6).

**Evidence recorded with every brainstorm and sprint-plan document**
- the requested profile and effort, and the observed ones separately, with
  run evidence when available (for example the CLI's recorded model id);
  unknown execution evidence stays unknown and is never inferred from a lane
  name or a policy label;
- the digest of the input plan, the ranking snapshot digest, and any
  `composer_unknown` or `composer_fallback` reason.

**What the rule is and is not**
- It chooses who *writes* the synthesis. The composer produces an artifact
  only: it cannot change signers or gates, and it cannot expand the approved
  scope. RCO review and the signature rules are unchanged.
- It reconciles Lead's round-1 point that a benchmark is only task-specific
  evidence: the external index is used for this one role, with provenance,
  because the operator directed it. Other task classes still rank by F21
  measurements.

### 2.11 Automatic operation and learning, like the waggle dance (R15)

These are **objectives with measured evidence, not guarantees**. The new
authorities they need are listed in §4 as proposed new authority.

**Automatic by default.** After the future signature (§4), routine operation
runs without operator intervention: switching, quota avoidance, routing,
restarts and learning. The operator can always freeze, override or read the
digest.

**Two kinds of stop, kept apart:**
- An **operational wait** (busy queue, a pool waiting for its reset, a
  composer waiting for capacity, a transient provider error) has an automatic
  exit: a bounded retry after revalidating the original bounds, a
  deterministic tie-break, or a route around it to *file-disjoint eligible
  work*.
- A **safety or authority HOLD** stays blocked until the actual condition or
  authority changes: an explicit operator freeze, hold or cancellation;
  missing authority; ambiguous identity; an integrity mismatch; exhausted
  qualified capacity. Principals cannot clear these automatically, and no
  retry or learned change can make an operator freeze expire.
- Nothing is ever routed around a claim, a veto or a gate.

**The swarm stays operational:** one lane's failure does not stop the rest.
Wedged claims are marked and other file-disjoint work continues (§2.9), Grok
is `skipped` when unavailable (§2.6), the supervisor restarts a dead executor
(§2.4), and quota risks are handled before they bite (§2.2, R13).

**Staying up to date** (proposed new authority, §4):
- F3 refreshes the registry automatically from its external sources, using
  each source's own measurement date.
- A new model or version becomes a **scout candidate**. The qualification
  harness (F21) measures it on **isolated synthetic or replayed tasks only**:
  no production writes, no secrets, no decision authority.
- A candidate joins the envelope only through a **bounded admission policy**
  that the future signature names explicitly. The policy defines eligible
  providers and accounts, data destinations, immutable model and version
  identity, allowed capabilities, spend and quota caps, evidence freshness,
  an independent evaluator quorum, de-admission and rollback. A new provider,
  a new data egress or a paid tier is outside the policy and needs its own
  signature.
- Receipts expire, and are re-measured when a provider version changes.

**Learning, like the waggle dance.** A honeybee scout that finds a good food
source dances to tell the others its direction and quality; more foragers
follow a better source; a stop signal warns off a bad one; and old
information fades. The swarm does the same with models and routes:
- **Scouts.** Candidates are explored only in isolation (above). Among
  *admitted* profiles, a small signed share of routine and implementation
  work explores alternatives, to find better or cheaper routes.
- **The dance.** Every finished task publishes an outcome record to the
  shared routing ledger: class, profile, success, attempts, pool cost and the
  verifying tests. That record is the dance: the quality and cost of one
  "food source" (a profile for a task class).
- **Recruitment.** Routing weights rise with successful outcomes and fall with
  failures. Only genuinely independent evaluations count: outcomes are
  deduplicated, and self-grading is excluded. Weights decay over time, so old
  news fades.
- **Stop signal.** A failure, a limit hit or a quality regression
  quarantines that profile for the class until it is requalified; a few
  self-reported successes do not lift it.
- **Quorum.** A routing change that affects many lanes, and every admission,
  needs independent evaluations from at least a signed number of evaluators,
  like the quorum bees use to choose a new nest site.
- **Forecast learning.** Each at-risk forecast (R13) is compared with the
  actual pool use afterwards; the error recalibrates burn-rate and lead-time
  *estimates*.

**Bounds of learning.** Fixed hard guardrails (signed thresholds, ceilings,
budgets, floors, gates, signatures) are never learned. Learning moves only
routing weights and forecast estimates, within signed bounds. Every learned
change is an event with its evidence, and is rolled back automatically if a
stop condition trips.

### 2.12 Stand-in incarnation after a crash or an exhausted limit (R16)

§2.5 covers *planned* switches, which quiesce at a safe boundary and write a
checkpoint first. This section covers the unplanned case: the working model
crashes, or hits its usage limit mid-task, with no safe boundary and no fresh
checkpoint, and possibly with its own pool unusable until the reset. All of it
is **proposed new authority** (§4); nothing here is authorized now.

**Checkpoints.** There is always something to continue from, but work done
between two checkpoints can be lost; the plan measures that window and never
promises zero loss.
- The step journal of the plan-then-execute cycle (§2.7): one committed step
  at a time, with its status.
- A WIP checkpoint for the current step, written atomically at a signed
  interval and whenever a tool batch changes files. It records the base and
  head, the inventory of dirty tracked and untracked files, the hash of the
  WIP artifact (commit or saved diff), the head the tests last ran on, and
  the task revision. Secrets are excluded. A WIP commit is never called green.
- An **operation journal** for every action with an effect outside the
  worktree (push, merge, bridge message, deployment and similar): an intent
  with an idempotency key, then `attempted`, `applied`, `verified` or
  `unknown`.

**Detection.** The supervisor (not a peer) classifies the lane:
- *crashed*: the parent process is gone, verified by PID and start time;
- *limit-exhausted*: a **fresh** classified CLI limit error, correlated with
  the exact active task and turn and with a validated pool binding. An
  exhausted-pool reading alone only stops new admissions to that pool; it
  never stops a running process;
- *unresponsive*: alive with no progress proof past a signed bound
  (`wedged`, §2.9).

**Fencing, always complete.** A parent that is gone does not prove its child
tools and heartbeat writers are gone. Before any claim moves, for both crash
and limit failure, the single executor fences every writer. It checks exact
PID, start time, token, generation and owned descendants, and handles dead
parents with live children, recycled PIDs and unrelated descendants.
- For a live limit-exhausted owner, a cooperative checkpoint-and-stop with a
  bounded grace period comes first.
- A forced stop is allowed only with verified identity AND proof that it is
  safe: no active external write that cannot be classified or safely settled.
  Otherwise it is a safety HOLD. A quota reading is never kill authority, and
  freeze and HOLD take precedence.
- An *unresponsive* live owner is not taken over (B7 stays deferred, §5). It
  stays a safety HOLD, and only file-disjoint work continues.

**Incarnation.** After a complete fence, the executor starts a **stand-in**:
1. **Profile.** The best eligible profile for the task's class on a pool with
   headroom, possibly another family (§2.2 eligibility, atomic pool
   admission). A change of family is always the identity-changing path of
   §2.5. Task context is portable; the conversation is not.
2. **Readiness before writes.** For a new family, the stand-in's CLI
   capability, tools, data-egress policy, role bootstrap (F2) and request
   rebinding are verified before it may write. Until then it may only prepare
   read-only context. It never answers under the old bindings.
3. **Claims** move to the stand-in's token by the fenced CAS of §2.5, checked
   deterministically.
4. **Reconcile before continuing.** The stand-in reconciles the durable
   filesystem state and the external receipts against the operation journal.
   An unknown outcome of a non-idempotent action stays a safety HOLD. There
   is no exactly-once promise without an idempotency contract on the
   receiving system.
5. **WIP.** The existing WIP is preserved immutably. If the stand-in validates
   it, it continues from the next unfinished step. If not, it quarantines a
   copy and resumes in a new, scoped, persistent worktree from the last
   verified checkpoint. It never resets or overwrites the old dirty tree, and
   never overwrites unrelated user or peer edits.
6. **Requests** bound to the old identity are never transferred: the executor
   posts `identity_changed` and the requesters reissue (§2.5).
7. **Authority.** A stand-in inherits no RCO vote, veto clearance, signature
   or privilege. Vetoes and findings from the old identity stay in force
   until the authorized process resolves them; a fresh review is not a
   clearance. Scheduling keeps both RCOs from being unavailable at the same
   time.

**Handing back.** When the original lane's pool recovers, the stand-in
finishes its current step and hands the task back at that step boundary, by
the same checkpoint, fence and CAS path; or it keeps the task if the forecast
(R13) says so.

**Limits.** The retry budget and circuit breaker are per task and shared
across stand-ins, so a change of identity never resets them. Ambiguous
identity, an incomplete fence, an unknown external outcome, or no eligible
profile with headroom is a safety HOLD. A stand-in is never a way around a
claim, a veto or a gate.

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
| F2 | 1 | One versioned bootstrap contract + prompt lint | L | fresh and resumed sessions report the same hash; a mid-task session survives the change | wrong or missing hash means fail closed | canary one lane, previous hash valid until each session moves | previous contract hash and text together |
| F3 | 1 | Registry v2 + stored pool cost + collector provenance | T | meter matches operator pool readings on 7 days of data, within a tolerance frozen in the packet before the run; rows failing the schema are counted as an unknown residual, never dropped | unknown stays unknown | deploy | data revert |
| F4 | 1 | Grok measurement and ledger | T | every call in the window is in the ledger | unclassified error means cooldown | deploy | helper pin |
| F5 | 1 | Lock-participant evidence | T | runs before activation and after reboot | more than one logon or integrity means HOLD | gate for Stage 5 | n/a |
| F6 | 1 | Read-only dashboard | T | matches canonical revisions on a replay | unknown shown as unknown | deploy | n/a |
| F21 | 1 | Minimal qualification | F writes, T runs, RCOs evaluate | per profile: task-class coverage, repeats, sample size and uncertainty, provider, version and effort, provenance, freshness; adversarial holdout cases. Receipts are bound to the exact code, profile, provider version and freshness, and never claim universal intelligence or uninterrupted availability | no receipt means out of the envelope | before the catalog signature | n/a |
| F7 | 2 | One outstanding wake per lane | L | Lead's audit acceptance list on an isolated runtime, plus a backlog migration round trip that stays readable after a rollback to the relay | uncertain means reconcile, never resubmit blindly | canary: Tools only with verified ownership and no critical work; then Lead; then one Claude lane; then the rest one at a time | flag back to relay |
| F8 | 3 | Work-queue serialization + outbox (replaces #1567) | F | cross-runtime race harness: one winner, no resurrection; a kill between mutation and publish loses no event; killing the lock holder unblocks waiters with no unlocked mutation; an unbound replay case | busy means refuse | deploy | revert |
| F9 | 3 | Git-guard option parsing | F | probe matrix with success twins, including non-allowlisted `-c` keys and the `GIT_DIR`, `GIT_WORK_TREE` and `GIT_NAMESPACE` variables | unknown option means refuse | deploy | revert |
| F10 | 3 | Worker-bound lease, fencing, retirement, absolute cwd | F | a long task keeps its claim; a killed owner loses it; a finished task retires | unknown owner means no renewal | deploy | revert |
| F11 | 3 | `Reply-ToRequest` + requester supersede | F | binding equals `-ReplyToEventJson` on the corpus | ambiguous id means refuse | deploy | revert |
| F12 | 3 | Commit-status head validation | F | negative tests | reject | deploy | revert |
| F22 | 3 | Claim schema discoverability | F | examples and `-Explain` cover every resource kind | unknown kind means refuse (unchanged) | deploy | revert |
| F23 | 3 | Isolated reproduction of the suspected items | T | reproduction or a documented non-repro | a non-repro stays open until the packet accepts it explicitly | n/a | n/a |
| F13 | 4 | Explicit per-lane launch + preflight enforce | L | preflight equals D3 on every lane | mismatch means refuse | per-lane canary | previous qualified explicit profile and verified generation, else HOLD |
| F15 | 5 | Switching policy (pure) | F | property tests of every §2.2 guardrail; shadow coverage of every enabled pool and actuator path, plus negative cases: stale or missing quota, boundary caps, conflicting members and principals, duplicate at-risk intents, an author targeting its own reviewer, frozen override, reviewer protection, clock and expiry, crash and replay. A replay of 7 days of real pool data shows that the forecast would have acted before every limit hit where a qualified alternative existed (R13) | unknown means HOLD | shadow until coverage holds | flag off |
| F16 | 5 | Serialized executor (§2.4) | L | crash at each phase recovers without a double side effect; F5 green | ambiguous means HOLD, never kill | canary fable-5 (Lead observes, both RCOs available), then Tools and Lead, then each RCO when not reviewing affected work | flag off |
| F17 | 5 | Continuity state machine (§2.5) | F | a relaunch mid-task keeps its claims per path; requests are reissued, never transferred; a forged handover is rejected | no record means no successor | with F16 | revert |
| F18 | 5 | `wd-model` | F | end-to-end on the canary; enqueue only, no apply path | refusal prints the reason | with F16 | its own flag, independent of F16 |
| F19 | 5 | Task classes + brief-then-delegate + plan-then-execute + ledger | F | ledger shows cost per class; quality holds on F21 tasks; on the canary, a burst ends when its plan is committed, and the return profile equals the forecast's choice | no measurement means signed defaults only; a burst with no recorded plan ends at its cap | shadow ledger first | prompt table off |
| F24 | 5 | Composer rule (§2.10) | F | fixtures: top unavailable; stale, missing and incomparable scores; no eligible candidate; quota change before dispatch; freeze during the wait; duplicate requests; delegated composition refused when a lane switch would be. Every document records requested and observed profile, snapshot digest and any fallback | ineligible means skipped; unknown ranking means a labelled provisional synthesis or HOLD; no eligible profile means HOLD | with F19; the F2 bootstrap contract references the one source of the rule text | previous contract hash and rule text together |
| F20 | 5 | `grok_consult` + broker | T implements; F and RCOs test | none lost; the hourly guard is never exceeded by an autonomous call; an operator-requested call passes only with a session-observed, recorded instruction, and a relayed one is refused | unavailable means `skipped` | with Stage 5 | flag off |
| F26 | 5 | Automatic operation + waggle-dance learning (§2.11) | F writes, T runs, RCOs evaluate | fault injection: each operational-wait cause clears and work resumes with no operator action, while each safety HOLD stays blocked until its condition changes; replay: routing weights converge to the best measured route per class, a stop signal quarantines a failing profile within one tick, exploration stays within its budget, a candidate joins only through the admission policy with an independent quorum; adversarial: poisoned and replayed evidence, correlated lanes, self-grading, model-version drift and oscillation; no learned change exceeds a signed bound | a learned change without evidence is not applied; a bound breach rolls back automatically | shadow ledger and shadow weights first | learning off, last signed weights |
| F27 | 5 | Stand-in incarnation (§2.12) | L (with F16/F17) | fault injection on the canary: kill mid-step; a limit error mid-step; a stale, misbound or transient limit reading; PID reuse, a recycled child PID, a dead parent with a live child, unrelated descendants; partial WIP write, bad diff, untracked file; a crash just before and just after an external success and before its receipt; freeze during recovery; a partially applied CAS; two simultaneous stand-ins; hand-back failure. Records the observed recovery time and data-loss window | ambiguous identity, an incomplete fence, an unknown external outcome or no eligible profile means a safety HOLD; an unresponsive live owner is never taken over; a quota reading is never kill authority | with F16 and F17 | stand-ins off; the lane waits for its own pool |
| F25 | 1 | Shared quota visibility (§2.1) | T | every lane's boot brief and `wd-model status` show every pool with age and source; matches the F3 meter on a replay | unknown shown as unknown; no work routed to an unknown pool except urgent, with the reason | deploy (read-only) | remove the reader |

**Closures**, each only after a diff and test mapping, never by title alone:
- #1567, superseded by F8;
- #1638, superseded by F7 only if the diff-and-test map shows it;
- #1656, folded into F3 or closed;
- the #1751 slices, after main CI.

## 4. Proposed signature scope (a proposal, not an authorization)

**One signature is the target.** It depends on a complete frozen packet
satisfying every gate; if the packet is incomplete, the work does not proceed
on a partial signature.

**The packet binds:**
- head, tree, base, tag and release-notes SHA256. The RCO passes, the
  signature and the activation all name the same head and tree, and
  activation refuses any other (a squash merge is checked by tree);
- an absolute expiry timestamp, not a restatable "authorization time";
- F21 receipts regenerated at the frozen head;
- the signed parameters: trip lines, tick, hysteresis, forecast lead time,
  admission thresholds, measurement and exploration budgets, quorum size,
  learning bounds, Grok admission table and composer share;
- the SHA256 of:
  - the `CLAUDE.md` Rule 8 amendment;
  - the signed catalog (profiles with F21 receipts, the members and
    principals with their rights, the forecast lead time, the
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
- a maximum of 14 days, from the packet's absolute timestamp and never
  reset by a restart, for the stage to meet its coverage predicate. A stage
  that met its predicate stays on without renewal (R15), under the freeze,
  the kill switch and its automatic stop conditions. On expiry before the
  predicate is met:
  - a stage that was never activated stays off;
  - an enabled automation stops accepting new intents, and its in-flight work
    either settles safely or holds;
  - expiry and freeze never blindly kill lanes or undo durable completed
    effects;
  - reapproval is needed.

Freeze and the kill switch take precedence over every stage.

**Proposed new authority** (Lead, final round). These are material new
authorities. They are *not* covered by Lead's ecb3a643 confirmation or by the
#1751 signature, and the operator quotes in §0 are requirements for this
design, not authority. Each one is named explicitly in the future signature,
and the current gate code, holds and deployed rules stay unchanged until that
exact amendment is reviewed and signed:
- every-member switch intents (R13);
- the bounded automatic admission policy (§2.11);
- permanent operation after a dated qualification window, with continuous
  evidence freshness, stop conditions and revocation checks; expiry before
  qualification blocks activation;
- automatic stand-in incarnation after a crash or an exhausted limit
  (§2.12);
- the scoped single-use operator Grok exemption (§2.6).

**Optional bits.** The packet lists the policy features (the Rule 8
amendment, F19, F24 and the §2.11 learning) as separate bits, so the operator
could sign the queue and guard fixes without the policy changes. Mixed bits
need a dependency check, and there is never a partial signature over a
larger unsigned code scope. The proposal is all bits on, in one signature.

**Explicitly excluded:** Stage-2 cutover, Rule 9b activation, approval
carry-forward, any budget bypass, and fast or credit tiers. Merge authority
stays under Rules 9a and 9b. This packet does not change them.

**Measurements come before the catalog signature.** The minimum sample
sizes and tolerances for F3 and F21 are fixed before the evidence is
collected. Incomplete measurements give a smaller envelope, never a guessed
pass. Discussion and isolated tests need no extra approval.

The code-level slice map, the release runbook and the exact signature
binding are in `BRIDGE_V2_IMPLEMENTATION_MAP_20260928.md`.

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
  relaunch case, and §2.9 now detects a wedged claim and routes around it;
  automatic takeover of a live owner stays deferred.
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
- After the future signature, routine operation runs without operator
  intervention; an operational wait has a bounded automatic exit, while a
  safety or authority HOLD stays blocked until its condition or authority
  changes (R15). Nothing is routed around a claim, a veto or a gate.
- Learning moves only routing weights and forecast estimates, within signed
  bounds; hard guardrails, thresholds, gates, floors, ceilings and
  signatures are never learned.
- The executor is the only actuator, and a frozen request binding is never
  mutated. Every member may submit intents; none may act directly.
- The purpose is predictive: no pool reaches its limit while a qualified
  alternative exists, and no lane stops for quota reasons (R13).
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

**Addendum after round 3** (fable-5, 2026-09-28). Lead's round-3
confirmation covered head ecb3a643 only. After it, the operator's fifth and
sixth directives added the composer rule (R11: §2.10, F24) and quota
visibility with the operator-requested Grok exemption (R12: §2.1, §2.6, F25).
fable-5 wrote the addenda in a session whose recorded model id is
`claude-opus-5-5`; its effort level has no execution evidence.

**Final improvement round** (at the operator's request; Lead 04:51:02Z on head
a5425f12, Grok separately). Lead marked the composer addendum `modified`
(design only) and asked for: eligibility before ranking, comparable scores
only, a frozen snapshot, a labelled provisional synthesis or HOLD when the
ranking is unknown, a bounded wait, requested versus observed profile
evidence, the same admission controls for delegated composition, an
artifact-only composer, and measurements ordered before the catalog
signature with thresholds fixed in advance. All were adopted in §2.10, F24
and §4.

**Seventh directive (R13).** The operator then stated the purpose of the whole
package: no quota limit is ever hit, by forecasting, and every swarm member
may adjust any other member's model and effort as predictive maintenance.
§2.2 was widened from two principals to every member, through the same
executor and guardrails; the reviewer-independence rule was kept and
generalized to every member. This change comes after Lead's final review
and still needs Lead's confirmation.

**Eighth directive (R14).** Detailed sprint plans are made on a strong model,
then the lane returns to the quota-preserving model and implements. Added as
the plan-then-execute cycle in §2.7 and F19: the burst ends when the plan is
committed, and the return profile comes from the quota forecast.

**Ninth and tenth directives (R15).** Everything runs automatically without
operator intervention, stays operational and up to date, and learns like the
waggle dance. Added as §2.11 and F26; contests, HOLDs and rollbacks now have
deterministic automatic exits; profiles join the envelope automatically under
signed thresholds (the operator signs the rules once, not each profile); a
stage that met its predicate no longer expires. These change Lead's round-3
expiry detail and the per-profile signature, so they need Lead's confirmation.

**Final Grok review** (grok-4.7 xhigh, two parts of about 15-19 KB, on head
a5425f12; 24 proposals, all checked against the plan text). Adopted: known
values need provenance, TTL and a production source; every catalog model has a
row; executor-side revert and conserve classification; RCO effort-only
intents; a measurement budget for unmeasured profiles; one tier table and a
printed default expiry; `wd-model` enqueues only; in-session switches need
only a safe boundary; a deterministic CAS check; two state machines and no
rollback onto `native`; broker admission, composer share, bound replies and
kept stderr; `planning_synthesis` split from `planning`; expected-cost
routing with signed thresholds; both tests and brief checks; class conflict
resolution; a Rule 8 default; wake preemption and supersede-with-cancel; the
contract canary; the outbox; the git `-c` allowlist and the three GIT_*
variables; wedge detection; composer ties, epsilon and plausibility bound;
bound SHA and tree, absolute expiry, receipts at the frozen head; frozen
tolerances, unknown residual, a flag for `wd-model` and open non-repros.
Adapted to R15 instead of taken as proposed: reapproval on a composer winner
change (a plausibility bound instead), an operator-signed fence for a wedged
owner (detection and routing around instead; takeover stays deferred), an
HOLD for an unqualified resume (the identity-changing path instead),
rejecting a missing `--for` (a printed default expiry instead), a composer
that never uses a session switch (R14 needs the burst; the wait never holds
the lease), and default-off policy bits (offered as optional, proposal all
on). Kept from Lead's design rather than Grok's: the buddy fallback runs the
same executor under the same lock, rather than the supervisor alone.

**Lead on R12-R15** (05:02:23Z, head 6ba09e3b): `modified`, design only. All
objections and proposals adopted: operational waits separated from safety or
authority HOLDs (no absolute "every HOLD has an exit", nothing routed around a
claim, veto or gate); candidates explored only on isolated synthetic or
replayed tasks; one revert rule; evidence kinds separated instead of a
blanket production label; finite retries with a circuit breaker; independent
verification for the first live canary; atomic pool admission; return at a
safe boundary; a scoped single-use Grok exemption; hard guardrails never
learned, independent evaluations only, quarantine until requalification; and
the new authorities listed explicitly in §4 as proposed, not inherited from
ecb3a643 or #1751. Lead has reviewed the plan text, not independently
validated the Grok output or the external scores; Grok's content is advisory,
not peer approval.

**Eleventh directive (R16).** The operator asked whether a crashed or
limit-exhausted working model can be replaced by an incarnated stand-in. §2.5
only covered planned switches, so §2.12 and F27 were added: continuous
checkpoints (step journal and WIP record), supervisor-side detection, an
automatic stand-in on another eligible pool through the identity-changing
path, claims by fenced CAS, continuation from the next unfinished step,
no inherited authority, and hand-back at a step boundary. An unresponsive
live owner is still not taken over (B7 stays deferred).

**Lead on R16** (05:17:15Z, head 84d0037e): `modified`, reviewing the R16 diff
only. All adopted in §2.12 and F27. Verified identity alone is not authority
to kill a live worker, so a forced stop also needs proof of safety, a
cooperative stop comes first, and a quota reading is never kill authority.
Every writer is fenced, including after a crash. A fresh limit error must be
correlated with the task and pool. An operation journal and a reconcile step
cover external effects, with no exactly-once promise. WIP is preserved
immutably and quarantined rather than reset. A new family must be verified
ready before it writes. Old vetoes stay in force. The retry budget is shared
across stand-ins, and more fault-injection cases were added. The
data-loss window between checkpoints is measured and not denied.
