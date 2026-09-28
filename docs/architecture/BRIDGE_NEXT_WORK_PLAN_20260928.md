# Bridge next work plan (after #1751)

Status: design for review. **Not implemented; no runtime path in this document
exists yet unless it is marked "exists".** It grants no authority and changes
no gate. Author: fable-5, 2026-09-28, at the operator's request. Implementation
starts only after the #1751 rollout has finished and been verified.

## 0. The operator's requirements

The operator's words, verbatim including typos (2026-09-28, Finnish; the
requirements table below restates them in English):

> välitä lista leadille sellaisilla muuttoksilla että mahdollisimman käyttäjä
> ystävällinen mallin vaihto on mahdollista ja ne kekskeneräiset ovat sinun
> suunnittelemana suunniteltu loppuun ja miten ne istuvat nykyiseen. Bridgen
> pitää olla tietoinen jokaisen eri mallin käyttörajasta ja kustannuksesta,
> Grok pitää olla käytettävissä ja ja kaikki mallit niin etteivät ne
> perustyössä käytä kalleinta mallia mutta suunnitelu ja brainstormeissa
> pystyvät käyttämään mallia joka on tehhokas.

| # | Requirement | Where in this plan |
|---|---|---|
| R1 | Switching a model is as user-friendly as possible | §3 |
| R2 | The bridge knows every model's usage limits and cost | §2 |
| R3 | Grok is available (to every lane, not only Lead) | §4 |
| R4 | Routine work never uses the most expensive model; planning and brainstorming may use a strong one | §1, §5 |
| R5 | The unfinished bridge items are designed to completion, showing how they fit today's code | §6 |

A follow-up the same day, also verbatim:

> Niin tai tässä kokonaisuudessa täytyy olla toiminnallisuus, että esim fabel
> tai lead voi vaihtaa mitä tahansa mallia tai mallin efforttia perustuen
> pyyntöön, omaan arvioon tai mittaukseen. Eli parven päämiehillä on
> mahdollsuus autonomisesti säätää toimintaa ja heillä pitää olla tiedossa
> jokaisen olemassa olevan mallin äly kustannus ja jos omaa tilaa ei pysty
> muuttamaan sen muutostyön voi pyytää toiselta mallilta. Ja sitten perustyö
> pitää pystyä arvioida mihin malli kannattaa siinä muuttaa että toiminta on
> kustannustehokasta, mutta parhaalla mahdollisella alustuksella

| # | Requirement | Where in this plan |
|---|---|---|
| R6 | The swarm's principals (for example fable-5 or Lead) can autonomously switch any model or effort, based on a request, their own assessment or a measurement | §3.5 |
| R7 | The principals know every existing model's intelligence and cost | §3.7 |
| R8 | A lane that cannot change its own state can ask another model to make the change | §3.6 |
| R9 | Routine work is routed to the most cost-effective model, but prepared with the best possible initialization | §5.4 |

This plan continues the dynamic model system plan v4 and the lane-profile
switching spec v3. Both still live only in fable-5's `.codex-audit/`; their
build order is folded into §7 so nothing is duplicated.

## 1. What exists today (measured 2026-09-28 at main c5f7c933)

**Model selection**
- Every lane launches with `"model": "native"` and `"effort": "native"`
  (`ops/windows/reboot/wd-fleet.json`).
- Codex lanes therefore read the shared `~/.codex/config.toml`. Claude lanes
  read `~/.claude/settings.json`, or the transcript's model on `--resume`.
- A `/model` in one window becomes the default for every sibling lane
  (`docs/BRIDGE_EFFECTIVE_MODEL.md`). **This is the root cause of unfriendly
  switching**, and PR-9 (explicit per-lane launch) is the prerequisite for R1.

**Live pieces**
- The launch probe and preflight (alert-only).
- fable-5's auto-compact window.
- The capacity observer (`observations.sqlite`, Claude statusline and Codex
  `rateLimitsByLimitId`).

**Shadow only** (`execution_allowed: False`)
- The lane catalog (unsigned; every profile has `approved: false`).
- Pacer, registry, planner, relaunch executor (no production ports), capacity
  advisor, and cost meter (#1747, stdout only).

**Registry** (`configs/model_registry.json`)
- Rows: claude-fable-5-1, claude-opus-5-5, claude-sonnet-5; codex
  gpt-5.6-luna/sol/terra and gpt-6-astra/sol/luna.
- Fields: benchmark intelligence and `usd_per_task` per effort.
- Missing: Haiku and Grok rows; limits, quota pool / `limit_id`, context window
  and measured cost.

**Grok**
- The only live path is the pinned hourly CLI helper
  (`tools/wd_grok_helper.py`, one attempt per 60 min, fleet-wide).
- "Lead only" is a prompt rule, not enforced.
- Results always go to `codex-lead-1`.
- stderr is discarded, and the helper records no tokens or model.
- `waggledance/core/bridge_workflow.py` refuses to route Grok through the bridge
  queue.
- The HTTP path (`tools/invoke_grok_review.py`, `configs/grok_budget.json`) is
  dormant and uses a stale model.

**Task-class routing** is specified (plan v4 demand levels, PR-11) but not
implemented. The catalog knows only the `wd-review` and `wd-routine-coding`
qualification classes.

## 2. Limits and cost awareness (R2)

### 2.1 Registry v2 fields

These are data only, class (b). They extend `configs/model_registry.json`.

| Field | Meaning | Source |
|---|---|---|
| `pool` | the quota bucket the model draws from, keyed by provider `limit_id` (Claude: `five_hour`, `seven_day`, plus a family limit `opus`/`sonnet`; Fable: nested cap of 50 % of the weekly pool on Max; Codex: the shared pool, per `limit_id`; Grok: `grok-weekly`) | vendor docs, observer |
| `relative_cost` | points per million weighted tokens, measured per profile | PR-13 cost meter, daily |
| `price_ratio` | API price ratio, used until a measurement exists | vendor price pages |
| `context_window`, `auto_compact_default` | tokens | vendor docs |
| `tier` | `economy` / `standard` / `strong` / `premium` | derived: premium = most expensive per family (Fable 5.1, gpt-6-astra) |
| `measured_at_utc`, `source` | provenance of every number | - |

New rows:
- `claude-haiku-4-5`, economy tier.
- `grok-4.7`, with effort `low` to `max` and `pool=grok-weekly`.
- `grok-4.7-build-fast` stays listed-only.

The registry never contains a secret or an account id.

### 2.2 Capacity board

One read-only command merges the observer, pacer and cost meter into one screen:
`tools/wd_capacity_board.py`, exposed as `wd-model status` in §3. It runs
fleet-wide:

```
POOL             USED  RESET    FORECAST   MODE       COST/Mtok (measured)
claude-weekly     41%  3d 02h   on_pace    steady     opus-5-5 1.00 | sonnet-5 0.20 | haiku 0.05
claude-5h         12%  3h 40m   underused  underused
codex-primary     35%  2h 10m   on_pace    steady     sol 1.00 | luna 0.05 | astra 2.4
grok-weekly      ~30%* 4d       unknown    hold       grok-4.7 high  (*operator reading 5h ago)

LANE          PROFILE (tier)                 SOURCE        OVERRIDE
codex-lead-1  gpt-6-sol/high (strong)        explicit      -
codex-tools-1 gpt-6-sol/medium (standard)    explicit      -
claude-rco-1  claude-opus-5-5/medium (strong) explicit     -
fable-5       claude-opus-5-5/high (strong)  explicit      planning until 14:00Z
```

The numbers above illustrate the layout only.

**Fixes this needs** (class (b) unless marked):
1. The collector writes `account_pool = None`, so every binding is
   `unverified`. Fill it from the lane manifest, never from free text.
2. Store the cost meter's `r(profile)` daily in `observations.sqlite`
   (a new table). The pacer reads it instead of needing a hand-supplied value.
3. Pace Codex `secondary` windows as well as `primary`, if the observer sees
   them.
4. Grok: see §4.4.

## 3. Operator-friendly model switching (R1)

### 3.1 Principle

The operator states **what** (a lane, or all lanes, plus a tier and a
duration). The bridge does **how**: pick the profile, choose the safe
boundary, switch, verify, and revert on expiry. No config file editing and no
`/model` in a lane window. Once PR-9 lands, `/model` would be overwritten at
the next launch anyway.

### 3.2 The command

The command is `ops/windows/reboot/wd-model.ps1`, with a Finnish alias
`wd-malli`:

```
wd-model status                               # the capacity board (§2.2)
wd-model set fable-5 planning                 # strongest planning profile the lane allows
wd-model set fable-5 planning --for 2h        # auto-revert after 2 h (default 8 h)
wd-model set all economy                      # every lane to its floor
wd-model set codex-lead-1 gpt-6-sol/high      # an exact catalog profile
wd-model reset fable-5 | all                  # back to catalog defaults
wd-model freeze | unfreeze                    # kill switch for all automatic switching
```

Tier names:

| Tier | Finnish | Meaning |
|---|---|---|
| `economy` | `säästö` | the lane's floor |
| `standard` | `perus` | the catalog default |
| `strong` | `vahva` | one step above default |
| `planning` | `suunnittelu` | the lane's burst profile, time-capped |

Each tier maps to a catalog profile per lane, so the operator never has to
remember model ids.

### 3.3 What `set` does

1. **Validate.**
   - The target must be approved in the signed catalog and in the lane's
     `allowed_profiles` or `burst_profiles`.
   - `planning` is time-capped: proposed default 2 h for an operator
     override. The automatic loop's own bursts stay at 30 min (plan v4).
   - It is refused if the pool is in `conserve` mode. The refusal prints the
     forecast and offers `--force-budget`; the operator's own terminal only,
     see 3.4.
2. **Record.**
   - Write an override record
     `.agent-bridge/lane_profiles/overrides/<lane>.json` (schema
     `wd.lane-profile-override.v1`) with lane, profile, tier, reason, created
     and expiry times, and the channel.
   - Post a `decision/profile_override` bridge event to `operator` and the lane.
3. **Apply at the next safe boundary.**
   - An in-session switch (PR-12, M2: `/model` plus `/effort` delivered as a
     control turn) where qualified.
   - Otherwise a relaunch with an explicit profile (PR-3 executor + PR-9),
     resuming the conversation.
4. **Verify.**
   - The next turn's transcript or rollout must show the new model and effort
     (D3).
   - On a mismatch: revert, raise `switch_unverified`, and pin the lane until
     the operator acts.
5. **Revert on expiry.** This takes priority over everything and skips dwell.

The automatic loop (plan v4) treats an unexpired override as a pin. It may
step a pinned lane down only when the pool is in `conserve` **and** the
projection would pass 95 %. It then records why.

### 3.4 Who may switch

| Channel | May do | Why |
|---|---|---|
| Operator's own terminal (`wd-model ...`) | everything in 3.2, including `--force-budget` | direct operator action |
| Operator typing in a lane's own interactive session (e.g. "vaihda fable suunnittelumalliin 2 tunniksi") | `set` / `reset` / `freeze` within the approved catalog, no `--force-budget` | session-observed direct operator input, the same standard as the #1751 signature. The lane runs `wd-model` and quotes the operator's words in the event. |
| A principal (fable-5, codex-lead-1) on a request, its own assessment or a measurement | switch any lane, including itself, to any model and effort inside the operator-signed envelope, under the guardrails in §3.5 | operator directive R6 |
| Any other lane | a `profile_request` to a principal or the loop (for itself, one named task, with a reason) | only principals switch |
| A relayed "the operator said..." from another agent | nothing | a peer relay is not operator authority |

Raising a ceiling, approving a new profile or changing the fleet mode are not
part of `wd-model`. Those stay catalog signatures.

### 3.5 Autonomous adjustment by the principals (R6)

**Who.** The signed catalog lists `principals: [codex-lead-1, fable-5]`. Only
these lanes switch other lanes, or themselves, without a per-switch operator
action.

**What.** Any model and effort inside the **envelope**. The envelope is the set
of registry models and efforts the operator enabled with one catalog
signature.
- Proposed first envelope: every model and effort in the registry today, plus
  Haiku 4.5 and Grok 4.7.
- Excluded from the envelope: fast and priority tiers, and anything billed to
  pay-per-use credits (for example Fable on Pro, or Claude Fast mode). Those
  are real money.
- New models enter the envelope only through discovery, qualification (PR-16)
  and the operator's signature (plan v4 §6).

**Triggers.** Every switch names exactly one:
- `request`: a bridge `profile_request` event, or the operator;
- `assessment`: the principal's own judgement, with the task class, the
  expected benefit and the time cap written down;
- `measurement`: a pacer or demand-sensor verdict (PR-5, PR-11).

**Guardrails.** The actuator enforces these mechanically; a principal cannot
waive them:
1. **Budget.** The plan v4 check applies: the projection must stay at or under
   70 % for a steady switch, 90 % for a burst and 95 % for a sprint. The
   premium tier is only ever a time-capped burst. Without a measured cost, a
   switch may lower but never raise.
2. **Rate.**
   - Dwell of 30 min per lane.
   - At most 4 switches per lane per hour.
   - One change per pool per tick.
   - Hysteresis for raises.
   - A revert skips all of these.
3. **Reviewer independence.**
   - RCOs choose their own effort within their allowed range.
   - A principal cannot lower an RCO below the reviewer default.
   - A principal cannot change the profile of an RCO that is reviewing the
     principal's own PR.
4. **Operator precedence.**
   - An operator override pins a lane (§3.3), and `wd-model freeze` stops
     every principal.
   - Between the two principals, a switch made by one holds for its dwell. The
     other principal may change it within that time only in `conserve`, and
     every contested switch goes into the operator digest.
5. **Evidence.**
   - Every switch is a `decision/profile_switch` event with the actor, trigger,
     inputs, from and to profile, and time cap.
   - It is verified by D3 on the next turn and reverted on failure or expiry.

**Still not allowed:** changing the envelope or a ceiling, approving a model,
buying credits, enabling fast mode, changing the fleet mode, or lifting a
freeze. Those remain the operator's.

### 3.6 When a lane cannot switch itself (R8)

Some switches cannot be made from inside the lane:
- a Codex lane cannot relaunch its own process;
- no lane can verify its own relaunch;
- the in-session switch (PR-12) may not be qualified for a CLI version.

**Design**
1. The lane (or a principal acting for it) posts a `profile_change_request`
   with lane, target profile, trigger and reason. The lane then writes its
   checkpoint at a safe boundary.
2. **One actuator executes it.** This is the relaunch executor (PR-3) with
   production ports, run by the supervisor outside every lane. It checks the
   guardrails in §3.5 and applies:
   - the in-session switch where it is qualified;
   - otherwise an explicit relaunch that resumes the conversation.
3. **Buddy fallback.** If the actuator is unavailable, the other principal runs
   the same executor for that lane: Lead switches fable-5, fable-5 switches
   Lead. It uses the same checks and the same events. A lane never kills a
   peer's process outside the executor.
4. **Verification.** Someone other than the switched lane verifies the result
   by D3 and posts it: the actuator, or the other principal. The requester
   gets a bound reply.

Lead's self-transition (spec v3 B9, which has no trigger today) becomes this
path: Lead requests, the actuator applies, and fable-5 verifies.

### 3.7 What the principals know about every model (R7)

`wd-model models [--json]` is the principals' decision table. It is also
injected, compacted, into every principal's boot brief. It has one row per
model and effort:

| Column | Source |
|---|---|
| provider, family, tier, pool / `limit_id` | registry v2 (§2.1) |
| intelligence and coding index | registry (benchmark, with version) |
| measured quality per task class | PR-16 qualification suite and the §5.4 routing ledger |
| relative cost (measured points per Mtok) and price ratio | PR-13 cost meter, stored daily |
| pool state now (used, reset, forecast, mode) | observer + pacer |
| available in the CLI now; in the envelope | CLI model caches, signed catalog |
| value = quality per cost for each task class | derived; the ranking the principals use |

Unknown values are shown as unknown, never guessed. A decision that depends on
an unknown value may lower but never raise.

## 4. Grok for every lane (R3)

### 4.1 Request kind

- Add a bridge request kind `grok_consult` that any lane may post.
- Payload fields: `purpose` (`review` | `arbitration` | `research` |
  `brainstorm`), `effort` (default `high`), a `prompt_ref` (a file under the
  requester's worktree or `.codex-audit`, ≤ 24 KB), `deadline_utc` and
  `priority` (demand 0-3).
- This replaces the refusal in `bridge_workflow.py`, which is an (a)-class
  change because it touches the bridge workflow. The `requester must be Lead`
  rule becomes a budget and priority rule.

### 4.2 Broker

- The existing helper stays the only caller of `grok.exe`. A single
  `grok-broker` consumer (the Tools consumer pattern) takes queued
  `grok_consult` requests in priority order and calls the helper.
- The result is posted as a **bound reply to the requester** (not always
  `codex-lead-1`), with the report path and SHA256.
- The helper records the calling lane; the wrapper no longer hard-codes Lead
  as the recipient.

### 4.3 Budget and graceful skip

- **Admission.** The fixed "one attempt per hour" becomes a paced token budget
  (PR-15b):
  - weekly budget = the calibrated `grok-weekly` estimate × a safety factor;
  - per call, the admission check `used + estimate(call) ≤ trip line` must hold;
  - per-lane fair share, with priority by demand;
  - the one-per-hour limit remains the floor until the calibration is signed.
- **When Grok is unavailable** (limit error, auth, timeout, budget exhausted),
  the requester gets a machine-readable reply
  `status=skipped, reason=grok_unavailable:<class>` and carries on.
  Grok is **never a gate** (operator decision 2026-09-27).
- **Failures:**
  - classify stderr as `limit`, `auth`, `max_turns`, `timeout` or `other`
    (the calibration runner already does this);
  - an `auth` or `limit` failure opens a cooldown until the next operator
    reading or reset;
  - a `max_turns` failure is refunded once, because it is a prompt-shape
    error, not usage.

### 4.4 Measurement (PR-15a)

- The helper uses `--output-format json`. It records model, effort, session id,
  stop reason and tokens, joined with `~/.grok/sessions/*/signals.json`.
- The timeout scales with effort (300 s at `high`; 600 s at `xhigh`, measured
  400-535 s).
- Calibration and manual runs append to the same ledger, so the budget sees
  all usage.
- The used % is visible only on the account's Usage page. `wd-model grok-reading 35`
  records an operator reading, and the board shows its age.
- Retire the dormant HTTP path and `GROK_DEPLOYMENT_V1.md`, or mark them
  historical, so there is one budget authority.

## 5. Cheap routine work, strong planning (R4)

### 5.1 Task classes

Task classes are bridge facts, never free text. They come from the request
kind, labels, and the (a)/(b) class of the PR.

| Class | Examples | Claude default | Codex default | Grok |
|---|---|---|---|---|
| `routine` | bridge bookkeeping, replies, status polls, log/CI parsing, reformatting, mechanical edits | Haiku 4.5 or Sonnet 5 (via subagent) | Luna | - |
| `implementation` | ordinary code and tests | Sonnet 5 or Opus 5.5 medium | Sol medium | - |
| `review` | exact-head RCO review, (a)-class verification | Opus 5.5 at the reviewer default (never lowered while others can step down) | Sol high | advisory, before the RCOs |
| `planning` | design, brainstorm, research, arbitration, architecture | Opus 5.5 xhigh; Fable 5.1 only as a justified burst inside its 50 % weekly cap | Astra (burst) | grok-4.7 high/xhigh as the third family |
| `incident` | blocked merge, request past its deadline | plan v4 burst rules | same | optional |

**Premium tier = the most expensive model per family** (Fable 5.1, gpt-6-astra).
It is never a default and is used only by `planning`/`incident` bursts that
pass the budget check.

### 5.2 Two routing mechanisms

1. **Per-task delegation (fast, the biggest saving, lowest risk).**
   - A strong lane hands its mechanical subtasks to a cheaper model instead of
     running them itself. Claude lanes use the subagent `model` parameter
     (`haiku`, `sonnet`). Codex lanes use a headless `codex exec` on Luna;
     whether that draws the same pool at the Luna rate is **to be verified**
     with PR-13 before it becomes policy.
   - The lane's own context (the dominant cost, ~87 % cache reads for Claude
     lanes) stays small because tool output stays in the subagent.
   - Lane prompts gain a short routing table. The subagent choice is recorded
     in the event.
2. **Per-lane profile (slower).** The plan v4 loop, plus the §3 overrides:
   - a lane's profile follows its demand;
   - `planning` is a time-capped burst;
   - after the burst the lane returns to its default.

### 5.4 Brief-then-delegate: cheap execution, best initialization (R9)

Most routine and implementation work does not need the strongest model to
*execute*. It needs the strongest model to *set it up*. The pattern has five
steps.

1. **Brief (strong model, the principal).**
   - Write a task brief: goal, exact files and line references, relevant
     invariants and rules, known pitfalls, acceptance tests, the definition of
     done, and what the executor must not touch.
   - The strong model gathers the context. This is the "best possible
     initialization", and it is where the quality comes from.
2. **Route.**
   - Pick the cheapest model and effort whose measured success rate for this
     task class is at least the threshold. Proposed thresholds: 90 % for
     routine, 95 % for implementation.
   - Expected cost is `cost(model) x expected attempts`.
   - Until there are measurements, use the §5.1 defaults.
3. **Execute (cheap model)** in an isolated worktree or subagent, with only
   the brief as context. The executor has no bridge authority: no claims, no
   decisions, no merges.
4. **Verify (strong model or tests).** Run the acceptance tests and review the
   diff. Verifying is much cheaper than doing the work.
5. **Escalate on failure.**
   - Move up one tier (economy, then standard, then strong), adding the failure
     notes to the brief.
   - After two escalations, the principal does the task itself.

**Measurement closes the loop.**
- Every delegated task appends one row to a routing ledger: task class, model,
  effort, brief size, attempts, success, tokens and cost.
- The ledger updates the success rates that step 2 uses.
- The weekly digest shows cost per task class before and after.

This is how the routing becomes measured rather than guessed.

**Unchanged by this pattern:**
- (a)-class code still gets the full exact-head dual-RCO review, whichever
  model wrote it.
- Review, planning and incident work stay on strong profiles.

### 5.3 A rule conflict to resolve first

`CLAUDE.md` Rule 8 says the default for every session and every subagent
"whose model is not explicitly fixed" is the strongest Opus model.
- Per-task delegation **fixes the model explicitly**, so it is compatible with
  the letter of Rule 8.
- Making `routine` default to a cheaper model is a policy change. It needs an
  amendment to Rule 8, which is (a)-class and needs an explicit operator
  signature.
- Proposed wording: "Default is the strongest model for `review`, `planning`
  and `incident` work; `routine` and `implementation` subtasks use the
  task-class table in `BRIDGE_NEXT_WORK_PLAN_20260928.md` §5, and every
  downgrade is recorded."

## 6. Unfinished bridge items, designed to fit today's code (R5)

### 6.1 Cross-runtime work-queue serialization (replaces #1567)

**Still needed after B7.**
- `CreateNew` stops two claims on the *same* task id.
- It does not stop two claims on *different* task ids from both passing
  `check_scope_overlap` and both writing (a check-then-write race):
  `waggledance/core/work_queue.py` `claim_task`, and `Claim-AgentTask.ps1`.
- Release (`File.Move`) and heartbeat (temp + move) can also interleave.
- B7's CAS is an identity check, not a transaction.

**Design**
- **Lock.** One host-local `WorkQueueV1` named mutex per bridge root. The name
  is derived from the root's full path hash.
  - It is created only through the #1751 same-logon helpers
    (`BridgeNamedMutex.ps1`, `tools/bridge_named_mutex.py`), so it inherits the
    SDDL, the minimum access rights and the diagnostics.
  - #1567's own mutex creation is dropped.
- **Critical sections.** Claim (scope-overlap check plus write), force refresh,
  release (owner check plus move to `done/`), heartbeat (owner check plus
  lease write) and applied stale sweep.
- Dry-run reads stay lock-free.
- **Timeout.** It fails closed with a retryable
  `work_queue_busy` error. **It never proceeds unlocked.**
- An abandoned mutex (owner crashed) is taken over, then the full claim set is
  validated before any mutation.
- **Bridge events.** They are written **after** the lock is released, so a slow
  append can never hold the queue.
- **Tests.**
  - A cross-runtime race harness: PS 5.1, PS 7 and Python writers racing
    overlapping scopes. It asserts exactly one winner per overlap and no
    resurrected claim.
  - Abandoned-owner tests.
  - Kernel-name isolation per the #1751 fixture rules.
- **PR.** Class (a) (work-queue source). Build it fresh on main, reuse #1567's
  tests where they still apply, then close #1567 with a pointer.

### 6.2 Git-option bypass of the branch guard

**Problem.** `Invoke-BridgeGit.ps1` takes the verb from `GitArgs[0]`.
- `-C <path> switch`, `-c k=v switch` and `--no-pager switch` all skip the
  guard.
- Measured on main d26357e1 and on #1751, and disclosed as a known limitation.

**Design**
- Parse leading global options before verb detection.
- **`-C <path>`:** resolve it into the target directory and run the guard
  **against that target** (agents habitually use `-C`, so refusing it would be
  unfriendly).
- **Refuse on branch-moving verbs:** `--git-dir`, `--work-tree`,
  `--namespace`, and any `-c` key under `core.` or `include`.
- **Pass through with no effect on target identity:** `--no-pager`,
  `--paginate`/`-p`, `--no-replace-objects`, `--literal-pathspecs`.
- Refuse the environment route `GIT_CONFIG_COUNT`/`GIT_CONFIG_PARAMETERS`, as
  #1751 does for `GIT_DIR`.
- Unknown leading options fail closed on branch-moving verbs.
- **Tests.** The `gitverb_probe` matrix (control, `-C`, `-c`, `--no-pager`,
  `--git-dir`) in PS 5.1 and PS 7, each with a same-bound success twin.
- **PR.** Class (a) (a guard).

### 6.3 Lock-participant enumeration and integrity split (activation gate)

**Needed because** the #1751 acceptance requires the actual creator/opener
tokens to be enumerated before activation and after reboot. Lanes were
observed at High integrity and Tools at Medium (rco-2, 2026-09-28), and the
cause of the AccessDenied seen there is still unknown.

**Design**
- **Tool.** A read-only `Get-BridgeLockParticipants.ps1` lists, for each bridge
  lock holder:
  - PID and start time;
  - logon SID;
  - integrity level;
  - the exact lock name.
- **Verdict.** The tool exits non-zero when the participants span more than one
  logon session or mix integrity levels.
- **Probe.** A Medium process opens a mutex created by a High process with the
  #1751 SDDL. The result decides between:
  - (a) keeping everything at one integrity level (a launcher change), or
  - (b) an explicit mandatory label at creation (a security-policy change that
    needs its own review).
  Neither is chosen before the measurement.
- **PR.** The tool is (b) (read-only). Any label change is (a).

### 6.4 B7 break-glass

**Problem.** A claim whose owner is alive but hung, for example a heartbeat
job still running for a stuck lane, never frees. The sweep needs the lease
expired **and** the heartbeat dead.

**Design**
- `Release-AgentTask.ps1 -BreakGlass -Reason "<text>"` works only from a
  **bound operator session** (`AGENT_BRIDGE_AGENT=operator` with owner
  identity). Bound `system` stays refused, as in #1751.
- It moves the claim to `done/` with `break_glass: true`, the operator session
  id and the reason.
- It posts a `claim_break_glass` event to the owner, Lead and both RCOs.
- The owner's next heartbeat or refresh fails its CAS and stops writing
  (existing behaviour, asserted by a new test).
- The Python `release_task` gets the same path.
- **PR.** Class (a).

### 6.5 Smaller items

| Item | Design | Class |
|---|---|---|
| S10: an unbound identical replay can reopen a request | treat a byte-identical event (same `request_digest` and body) after closure as a duplicate, not a reopen, in both runtimes | (a) |
| PS/Python casing parity of payload Status/Message | one shared casefold rule table (JSON) read by both runtimes, plus a conformance corpus | (a) |
| 196 historical rows fail the strict schema | a read-only report with a digest allowlist of known legacy rows; no rewrite | (b) |
| Capacity advisor rejects allowlisted cross-account candidates | allow a different `account_pool` only when the candidate is in the lane's own approved allowlist | (b), advisor is shadow |
| Superseded slice PRs #1749, #1750, #1752, #1658, #1655, #1637 | close with a pointer to #1751 once main CI at c5f7c933 is green | housekeeping |
| Branch protection (the repo has none) | operator decision; a required-checks ruleset on `main` would make "PR-only" server-enforced | operator |

### 6.6 Governance items (operator decisions, (a)-class)

1. **Approval carry-forward for content-identical rebases** (`CLAUDE.md` 9a
   says it is not implemented).
   - Every rebase today costs a full re-review round; each wake of a Codex lane
     is paid.
   - Implement it as gate code with its own adversarial review. The diff
     against the new base must be byte-identical and mechanically verified. CI
     always reruns.
2. **Rule 9b standing consensus-sign.**
   - It stays dormant until #1393 and the amendment are bootstrap-signed, and
     the cause-B free-text latch in `tools/check_bridge_changes_requested.py`
     is fixed.
   - Drafts #1664 and #1657 exist. This plan does not propose activating it,
     only lists the preconditions.
3. **The Rule 8 amendment** in §5.3.

## 7. Build order

Order is by dependency. Every step ships in shadow first, where it applies.
Every (a)-class step needs an operator signature.

| # | Step | Class | Depends on |
|---|---|---|---|
| 1 | Close superseded slices; verify main CI at c5f7c933 | housekeeping | #1751 rollout verified |
| 2 | §6.2 git-guard option parsing | (a) | - |
| 3 | §6.1 work-queue serialization on the #1751 mutex helpers | (a) | - |
| 4 | §6.3 lock-participant tool (read-only), then measurement | (b) | - |
| 5 | §2.1 registry v2 rows and fields; §2.2 collector `account_pool`, stored `r(profile)` | (b) | PR-13 (merged) |
| 6 | §4.4 Grok measurement (PR-15a) | (a) | - |
| 7 | PR-8 catalog signature (profiles `approved`) | operator | 5 |
| 8 | PR-9 explicit per-lane launch + preflight enforce | (a) | 7 |
| 9 | §3 `wd-model` status (read-only) + override records + relaunch apply | (a) | 8 |
| 9a | §3.7 `wd-model models` decision table (read-only) | (b) | 5 |
| 9b | §3.5 principal authority + §3.6 actuator with production ports and buddy fallback; envelope signature | (a) + operator | 8, 9 |
| 10 | §5 task classes + per-task delegation in lane prompts; Rule 8 amendment | (a) + operator | 5 |
| 10a | §5.4 brief-then-delegate harness + routing ledger | (b), then (a) when wired into lanes | 10 |
| 11 | §4.1-4.3 `grok_consult` request kind + broker + paced budget (PR-15b) | (a) | 6 |
| 12 | PR-12 in-session switch; `wd-model` uses it instead of a relaunch | (a) | 8 |
| 13 | PR-11 demand sensor + decision policy (shadow) | (b) | 5, 10 |
| 14 | §6.4 break-glass; §6.5 S10 and casing parity | (a) | 3 |
| 15 | PR-10 boot profile, PR-16 qualification, PR-14 maintenance window | (a)/(b) | 12 |
| 16 | Fleet mode `approve`, then `auto` | operator, Rule 10 discipline | all |

**The operator sees the first user-visible result at step 9.** From then on,
switching is one command or one sentence in a lane window. The biggest cost
saving arrives already at step 10.

## 8. Invariants

- **Fail closed.** Unknown means hold; near a cap it means step down; never
  raise.
- **Grok is never a gate.**
- **Nothing automatic buys credits** or enables fast/priority tiers
  (Fast mode is real money).
- **The loop, the principals and `wd-model` cannot:** change the envelope,
  approve a profile, raise a ceiling, change the fleet mode or lift a freeze.
- **Only the listed channels switch.**
  - The operator, from their own terminal or by direct input in a lane window.
  - The two principals, inside the envelope and under the §3.5 guardrails.
  - A peer relay or an agent-composed `operator` event is never operator
    authority.
- **No lane lowers its own reviewer**, and no principal lowers an RCO that is
  reviewing that principal's work.
- **Every switch is verified** by the next turn's transcript/rollout and
  recorded as a bridge event with its inputs.
- **Kernel-name and runtime-root isolation** in every test of bridge code.
- **Exact-head dual-RCO review** for (a)-class work; Grok review is advisory
  and runs before it.

## 9. Converged package after brainstorm round 1 (supersedes §7 where they differ)

Round 1 with Lead: fable-5 request 04:34:53Z, Lead's bound reply 04:36:20Z.
The operator's directives (verbatim, 2026-09-28):

> brainstormaa leadin kanssa 3 kierrosta ja tee yhteenveto lopuksi
> kokonaisuudesta nyt tehdään kaikki parannukset kerralla ja mahdollisimman
> vähillä allerkijoituksilla ja stepeillä

> brain stormiin tulee ottaa mukaan myös muut keskeneräiset stepit mitä esim
> lead ja sinä löysitte bridgeen liittyen ja samaan toteutus kokonaisuuteen

### 9.1 Adopted from Lead's round-1 objections

1. **Sibling defaults.** "A `/model` in one window changes sibling defaults" is
   documented in `docs/BRIDGE_EFFECTIVE_MODEL.md` but not reproduced. It must
   be reproduced on the installed CLIs before it is called a bug. The explicit
   launch (F13) is needed either way.
2. **No budget refunds.** The `max_turns` refund in §4.3 is withdrawn. Every
   attempt consumes budget, as the hourly guard counts it today.
3. **Cost is quota, not API price.**
   - Cost is measured pool points per token (PR-13), never an API price.
   - A benchmark score is task-specific evidence with provenance, not a
     universal intelligence rank.
   - `account_pool` comes only from validated provenance, never from a
     manifest.
4. **Unknown cost switches nothing.** It does not permit an assumed downgrade
   either. A switch needs a known, qualified fallback, and reviewer and
   task-class floors are kept. This replaces "may lower but never raise" in
   §3.7 and §8.
5. **No budget bypass.** There is no `--force-budget` in this package. An
   environment identity or an agent-composed `operator` event is never
   operator authentication.
6. **Coverage, not elapsed time.** Activation needs coverage predicates, not
   elapsed time. A shadow run with too few decisions is inconclusive.
7. **One executor.** There is one serialized external executor with:
   - a durable intent and an idempotency key;
   - expected generation, PID and start time, and token identity;
   - checkpoint and idle preconditions.
   Principals submit bounded intents. The buddy fallback invokes the **same**
   executor only after its exclusive ownership is proven, never a second path.
   Unknown owner, logon or integrity means HOLD. Recovery distinguishes
   queued, applied and verified, and never kills on an ambiguous identity.
8. **Deferred:** approval carry-forward, break-glass, broad casing-policy
   changes, the full PR-10/14/16 frameworks and the app-server transport.
   Minimal qualification of every enabled profile is **not** deferred.

### 9.2 Acceptance table

**Owner abbreviations:** L = Lead, T = Tools, F = fable-5. Owners are proposals,
not assignments. Every row ships behind a default-off flag unless it is
read-only.

Every (a)-class row also needs:
- dual-RCO review at the exact head;
- independent tests; Tools never solely validates its own broker or registry;
- kernel-name and runtime-root isolation.

**Stage 1: measurement and contracts** (read-only or additive)

| # | Feature | Current behaviour | Change | Owner | Evidence threshold | Fail-closed | Activation | Rollback |
|---|---|---|---|---|---|---|---|---|
| F1 | Wake telemetry | relay rows, no watermark | event revision ids; enqueue, start, end and coverage times; pending count; wake and suppress reasons; no-op ratio; p50/p95 latency | L | numbers reproduce from the ledger on an isolated trace | unknown fields shown as unknown | on merge (read-only) | remove the reader |
| F2 | One versioned bootstrap/role contract | contradictory layers (3PACK roles, the HEADLESS prompt) | one contract with a version and hash, verified at session start; obsolete layers become historical; CI lints the active prompt set | L | lint passes; a fresh and a resumed session report the same hash | missing or wrong hash means fail closed | at rollout | previous contract |
| F3 | Registry v2 + stored cost | benchmarks only; cost meter on stdout | pool / `limit_id` with provenance; measured pool points per Mtok stored daily; tier; context; Haiku and Grok rows | T | the meter reproduces the operator's pool readings within tolerance on 7 days of data | missing value shown as unknown | on merge | revert data |
| F4 | Grok measurement | stderr dropped; no tokens or model | JSON output; model, effort, session and tokens; error classes; one ledger that also covers calibration and manual runs; no refunds | T | every call in a week appears in the ledger | unclassified error means cooldown | on merge | helper pin |
| F5 | Lock-participant evidence | none | read-only tool: PID and start, logon SID, integrity, name | T | runs before activation and after reboot | more than one logon or integrity means exit non-zero and HOLD | gate for Stage 5 | n/a |
| F6 | Dashboard | scattered | read-only: task, owner, exact head, state, pending gate, age, checkpoint freshness, known-noise classes; material changes only | T | matches canonical revisions on a replay | unknown shown as unknown | on merge | n/a |
| F21 | Minimal qualification | none | fixed small suite per enabled profile (seeded-bug review, test repair, doc truth): quality score plus measured cost | T runs, F writes the suite | every profile in the envelope has a receipt | no receipt means not in the envelope | before the catalog signature | n/a |

**Stage 2: wake backpressure** (the largest cost saver; built and tested first)

| # | Feature | Current | Change | Owner | Evidence | Fail-closed | Activation | Rollback |
|---|---|---|---|---|---|---|---|---|
| F7 | One outstanding wake per lane | 5 s debounce only; backlog drains as no-op turns | durable watermark plus dirty flag; typed notification; pinned drain helper; late replies, cancellations and vetoes never coalesced; no-op drains invisible to the operator | L | the audit's acceptance list (100 events while busy give 1 pending wake; urgent veto not hidden; 30 idle min give 0 model calls; crash recovery) on an isolated runtime | on doubt, deliver (never drop) | canary on Tools, then all lanes, each stage with stop conditions | flag off returns to the relay |

**Stage 3: queue and guard correctness**

| # | Feature | Current | Change | Owner | Evidence | Fail-closed | Activation | Rollback |
|---|---|---|---|---|---|---|---|---|
| F8 | Work-queue serialization (§6.1; replaces #1567) | check-then-write race across task ids | `WorkQueueV1` via the #1751 mutex helpers; claim, release, heartbeat and sweep inside the lock | F | cross-runtime race harness: exactly one winner, no resurrection | timeout gives `work_queue_busy`, never unlocked | at rollout | revert |
| F9 | Git-guard options (§6.2) | a leading option skips the guard | parse options; `-C` guarded against its target; `--git-dir`, `--work-tree`, `--namespace` and `-c core.*` refused on branch moves; `GIT_CONFIG_*` refused | F | the probe matrix with success twins | unknown option on a branch move means refuse | at rollout | revert |
| F10 | Lease bound to the real worker | a short-lived shell PID; Lead's claim expired after 532 s | heartbeat follows the long-lived session; renewed during legitimate work; quiet waiting is not death | F | a long operation keeps its claim; a killed owner loses it | owner unknown means no renewal | at rollout | revert |
| F11 | `Reply-ToRequest -RequestId` + requester supersede | agents hand-extract exact JSON; stranded requests stay open | the pinned helper fetches and binds; the requester can supersede its own request | F | binding identical to `-ReplyToEventJson` on the corpus | ambiguous id means refuse | at rollout | revert |
| F12 | Head validation on `rco_pass` | an abbreviated head silently fails its slot | writer rejects a non-40-hex head on decision events | F | negative tests | reject | at rollout | revert |

**Stage 4: registry use and explicit launch**

| # | Feature | Current | Change | Owner | Evidence | Fail-closed | Activation | Rollback |
|---|---|---|---|---|---|---|---|---|
| F13 | Explicit per-lane launch + preflight enforce (PR-9) | `native`; preflight only alerts | every launch passes its profile explicitly; a mismatch refuses the launch | L (one owner, one contract with F16) | preflight equals D3 on every lane after a relaunch | mismatch means refuse | per-lane canary | manifest `native` |

**Stage 5: policy and actuator** (last)

| # | Feature | Current | Change | Owner | Evidence | Fail-closed | Activation | Rollback |
|---|---|---|---|---|---|---|---|---|
| F15 | Switching policy (pure) | shadow advisor | envelope, principals, triggers, budget, rate, reviewer independence, operator precedence, known-qualified-fallback rule | F | property tests of every §3.5 guardrail; shadow decisions logged | unknown means HOLD | shadow until the coverage predicate holds | flag off |
| F16 | Serialized external executor | no production ports | durable intent, idempotency, identity preconditions, queued/applied/verified recovery, a process-tree job object (the task limit does not reach the child) | L | crash at each phase recovers without a double side effect; F5 evidence green | ambiguous identity means HOLD, never kill | single-lane canary (fable-5), then the others; both RCOs never together | flag off; manual relaunch |
| F17 | Session handover on relaunch | open requests and own claims stranded with the old session | the executor writes a handover record; claims move to the new session by CAS; requests accept the successor through the record; in-session switch preferred | F | a relaunch mid-task keeps its claims and answers its requests; a forged handover is rejected | no record means no successor | with F16 | revert |
| F18 | `wd-model` CLI | none | `status`, `models`, `set`/`reset` within the envelope with expiry, `freeze`; no budget bypass | F | end-to-end on the canary | refusal prints the reason | with F16 | n/a |
| F19 | Task classes + brief-then-delegate + routing ledger | none | the §5 classes; subagent/`codex exec` executors without bridge authority; escalation; ledger | F | the ledger shows cost per class; quality holds on F21 tasks | no measurement means the default table | shadow ledger first | prompt table off |
| F20 | `grok_consult` + broker | Lead-only helper; bridge refuses Grok | a request kind for any lane; one serialized broker around the existing helper; the hourly guard stays the admission rule; `skipped` replies; reply to the requester | T implements; F + RCOs test | a week of requests: none lost, budget never exceeded | unavailable means `skipped` | with Stage 5 | flag off |

**Policy files in the same packet:**
- the `CLAUDE.md` Rule 8 amendment (§5.3);
- the signed catalog: profiles approved by F21 receipts, principals, and an envelope without fast or credit tiers;
- the activation plan (9.3).

**Not in the package:**
- break-glass (F17 covers the common case);
- S10 and broad casing changes (current binding semantics are preserved);
- carry-forward, Rule 9b and Stage-2;
- the full PR-10, PR-14 and PR-16 frameworks, and the app-server transport;
- other Global mutexes: evidence only, via F5.

**Closed as superseded:**
- #1567 by F8;
- #1638 by F7, if Lead agrees;
- #1656 folded into F3 or closed;
- the #1751 slices, after main CI.

### 9.3 One signature, enumerated activation

The operator signs once. The single packet contains:
- head, tree, base, tag and notes SHA256;
- the digests of the catalog, the policy files and the activation plan;
- explicit exclusions.

The activation plan lists, per stage:
- the flag;
- the coverage predicate (for example, for F15: at least 50 shadow decisions
  across at least 3 pools and 3 lanes, zero guardrail violations, and the
  acceptance matrix passed);
- the canary lane, stop conditions and rollback;
- an expiry: predicates not met within 14 days leave the stage off until a new
  signature.

`wd-model freeze` and an operator kill switch take precedence over every stage.
Nothing activates on elapsed time alone.

**Steps:**
1. The #1751 rollout.
2. Build slices in stage order, file-disjoint, reviewed as they land.
3. Freeze; one isolated matrix; CI; dual RCO at the exact head; build
   consensus.
4. One signature.
5. Merge and staged activation, each stage self-verifying against its
   predicate.
