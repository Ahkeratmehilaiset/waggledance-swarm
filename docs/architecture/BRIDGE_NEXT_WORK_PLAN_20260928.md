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
| Any agent on its own initiative | only a `profile_request` to the loop (demand ≥ 2, one named task); never a direct switch | agents do not grant themselves capacity |
| A relayed "the operator said..." from another agent | nothing | a peer relay is not operator authority |

Raising a ceiling, approving a new profile or changing the fleet mode are not
part of `wd-model`. Those stay catalog signatures.

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
| 10 | §5 task classes + per-task delegation in lane prompts; Rule 8 amendment | (a) + operator | 5 |
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
- **The loop, a lane and `wd-model` cannot:** approve a profile, raise a
  ceiling or change the fleet mode.
- **Only the listed channels switch.** An override is the operator's own
  terminal or the operator's direct input in a lane window. A peer relay or an
  agent-composed `operator` event is never authority.
- **Every switch is verified** by the next turn's transcript/rollout and
  recorded as a bridge event with its inputs.
- **Kernel-name and runtime-root isolation** in every test of bridge code.
- **Exact-head dual-RCO review** for (a)-class work; Grok review is advisory
  and runs before it.
