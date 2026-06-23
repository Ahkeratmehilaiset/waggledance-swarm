# RFC: WD Bridge Throughput, Resilience & Pool-Decorrelation

**Status:** Draft (for operator + swarm review)
**Author:** fable-5 (producer), synthesizing FOUR independent analyses:
fable-5, claude-rco-1, claude-rco-2 (structural) + fable-5, claude-rco-2 & claude-rco-1
empirical worktree scans (all five folded in 2026-06-23).
**Date:** 2026-06-23

> Method note: each agent answered the same prompt independently, un-steered (no
> peer answer shared before responding), per the operator's diverse-lens request.
> The convergence below is therefore independent corroboration, not echo.

## 0. Motivating incident
The codex usage pool (which backs **both** `codex-lead-1` and `codex-tools-1`) hit
its **weekly** limit; the swarm **wedged** — two fully-consensus-ready PRs (#1368,
#1369) sit unmergeable, and the live consumer loop is burning what little remains.
The `claude` pool (rco-1/rco-2 + fable-5) has headroom but is structurally confined
to review/advisory.

## 1. Operator optimization criteria (the bar)
Maximize ALL of these *together*, **without weakening any fail-closed gate guarantee**:
1. **Faster** achievement of the WD goal (throughput to *maaliin*).
2. **Better** code quality.
3. Operator (Jani) in the loop as **little** as possible.
4. **Maximal** use of every agent's power with **minimum** waste/redundancy.

## 2. Diagnosis — tri-converged root causes
All three structural analyses independently identified the same core:

- **D1 — Pool-correlated build SPOF (root cause of the wedge).** Rule-9a build
  consensus = `lead(codex)` AND `tools(codex)`, both mandatory. The two "independent"
  build signers share **one failure domain** (the codex weekly pool). A mandatory-AND
  across perfectly-correlated identities → when the pool hits 0, *both* die together
  and every merge wedges. *(all three)*
- **D2 — Producer monoculture on the exhausting pool.** Authoring *and* build-signing
  both funnel through codex; the Claude pool can only fill the RCO slot → large idle
  capacity while the bottleneck pool does all the work. *(all three; criteria 1 & 4)*
- **D3 — The operator-gate structurally *incentivizes* the waste.** Off-allowlist =
  per-PR operator sign; the allowlist is narrow, so low-value template/proof scaffolding
  *self-merges* while real runtime features *bottleneck on the operator*. This incentive
  **produces** the self-referential recursion. *(rco-1's causal insight; criteria 1,3,4)*
- **D4 — Self-referential tooling recursion = motion, not progress** (the "wall-loop":
  template→index→summary→verifier loops, classifier whack-a-mole). *(all three + the
  operator's earlier wall-loop finding)*
- **D5 — Free-text status classification → gate friction:** phantom blocks from
  block-vocabulary in message-type statuses; stale self-blocks that outlive their cause;
  negation fail-opens. *(all three; #1368 is the first fix instance)*
- **D6 — Redundant compute:** agents re-run CI's full suite locally per head (CI is
  already the authoritative full-suite gate); + head-drift re-sign churn. *(fable + rco-1)*
- **D7 — Dual-RCO full redundancy** on low-risk PRs (full dual review only needed for
  denylist/gate-critical/author-is-RCO/fail-open-prone). *(rco-1; criterion 4)*
- **D8 — No burn governance:** burst-then-exhaust, spending the scarce pool on
  low-value work before week-end. *(rco-2)*

## 3. Empirical grounding (read-only worktree scans)
The abstract diagnosis named the burn "generically"; the scans found the concrete engine.

**claude-rco-2 (the live burn mechanism — HIGH):**
- **EF1 — usage-limit-blind retry loop.** `.agent-bridge/bin/Start-AgentBridgeConsumerLoop.ps1`
  (loop ~ll.226-281) runs a full codex `exec` each tick, records the exit code, then
  **ignores it** and just sleeps+loops. No usage-limit detection, no backoff. Result:
  **~1555 `consumer_tick_failed` of 1613 started, at ~64s cadence** — ~1555 wasted
  full-agent invocations against an exhausted pool, still climbing, flooding the shared
  bridge log everyone reads. *(this is actively burning the pool right now)*
- **EF2 — every poll is a full heavyweight LLM turn.** tools-1 runs non-`WakeOnly`, so
  EVERY ~64s tick spins a full codex agent merely to "read the bridge and maybe do one
  slice" — even when there is nothing to do.
- **EF3 — xhigh reasoning effort on routine ticks** (most expensive tier spent on polling;
  comes from codex config default, not the launcher args).
- **EF4 — worktree sprawl:** ~168 `codex-lead-1-*` + ~369 `codex-tools-1-*` = ~537 per-task
  worktrees, most from merged tasks → disk + slow enumeration + foreign-WIP hazard surface.
- **EF5 — the per-tick prompt mandates work every tick** ("claim highest-value task, do one
  bounded slice, then stop") → turns idle minutes into full-agent invocations.

**fable-5:**
- **EF6 — the operating contract drives local test-running.** `AGENTS.md` mandates
  "prefer runtime evidence over static guesses," "reproduce before concluding," "always
  report the final test status" — and gives **no** "trust CI for the full suite / run only
  affected tests locally" guidance → contract-level driver of redundant local full-suite runs.
- **EF7 — a second auto-spawn loop:** scheduled task `WaggleDanceIdleLoopOnce` fires
  **every 30 min** (`WaggleDanceIdleDispatcher.ps1` → `tools/idle_loop_once.py`),
  independent of EF1's consumer loop — a second idle engine with no burn-budget gate.
- **EF8 — a non-codex pool exists but is DISABLED — *though it is NOT yet a build signer*.**
  Scheduled tasks `WD-GrokDispatcher`, `WD-GrokScout-Daily`, `WD-GrokRedteam`,
  `WD-GrokResearch`, `WD-GrokWatchdog` (`Invoke-Grok*.ps1`) all exist and are **Disabled**.
  **Correction (rco-1):** these are *auxiliary* scout/research/redteam roles — **none is wired
  as a gate `build_consensus` signer.** So enabling them provides non-codex *capacity* but does
  **NOT** by itself make Grok fill the tools build slot; the build-slot fix is re-backing the
  tools *builder identity* with a non-codex model (see §5 P0). *(grok superheavy also slated to join 2026-07-01)*
- **EF9 — the merge-driver: LIVE loop is fine; only the SCHEDULED copy is broken (latent, not active-wedge).**
  **Correction (rco-1):** the LIVE driver loop is a separately-launched PID-resident process
  running `Invoke-BridgeMergeDriver.ps1 -Loop -PollSeconds 120 -Apply -MaxMergesPerRun 1 -ExcludePrs 1261`
  — it IS applying (it merged today's PRs). The *scheduled* `WD-BridgeMergeDriver` (last ran
  2026-06-16, `lastResult=0xFFFFFFFF`, empty `NextRunTime`, action **without `-Apply`/`-LeadCosignWaiver`**)
  is a stale **duplicate** that either no-ops or RACES the shared `_wd_tools_current` worktree.
  So EF9 is **latent fragility** (no auto-restart/liveness, two launch paths, scheduled copy can't
  merge) — **NOT** the active-wedge cause. The active wedge is purely pool-exhaustion (no build
  signer can post). Prioritize P0 (pool decorrelation) over driver repair.

**claude-rco-1 (the burn is by-design fixed-cadence, and the fix is one flag):**
- **EF10 (highest-ROI — the dominant, demand-independent drain):** both codex consumer loops
  run `-Forever -PollSeconds 60` with **NO `-WakeOnly`** → a FULL codex `exec` tick **every
  ~60s forever, regardless of demand** (~1,440/agent/day, ~2,880/day combined). The loop is
  NOT event-driven even though the script supports it. And the per-tick prompt ("claim the
  highest-value task / do one bounded slice") means that when there is no real work the agent
  **manufactures** it (template/proof/scout) — so this single cadence is the empirical engine
  of **both** pool-exhaustion **and** the self-referential recursion. Fix: launch the loops
  with **`-WakeOnly`** (already supported; `Watch-Bridge.ps1` writes the wake sentinel on real
  targeted traffic) or idle backoff. **Must sequence ALONGSIDE P0, not after** — a 60s-forever
  cadence drains whatever pool backs it, so `-WakeOnly` is needed even after pool-decouple.
- **EF11 (delta-A — tick stacking, worse than one-waste-per-minute):** the tools loop sets
  `-CodexTimeoutSeconds 900` (15 min) with `-PollSeconds 60`, so a tick can run up to 15× the
  poll interval while new ticks keep firing → ticks **overlap and stack concurrent codex
  invocations** (matches `consumer_tick_timed_out` events). Fix: the EF1 exit-code backoff +
  a **single-in-flight guard** (one tick at a time).
- *EF12–EF14 corroborate EF6/EF9/EF4:* local repro double-executes what CI runs on the codex
  pool (scope local repro to the minimal failing case; let CI prove the matrix); the live
  `-Apply` driver confirms EF9's reframe; and the tools loop runs from a *bridge-monitor*
  worktree launched from yet another *consumer-loop-source* worktree → which worktree is
  canonical is ambiguous (config-drift on top of EF4 sprawl).

## 4. The one decision for the operator (a genuine peer divergence)
**How to decorrelate the build pair (fix D1):**
- **rco-2 — *widen* the builder-eligible set:** allow a Claude-pool builder (e.g. fable)
  to fill a build slot for charter-clean PRs. Flexible/scalable; requires the
  **one-slot-per-identity** guard (an RCO filling a builder slot must NOT also fill the
  RCO slot, or 3-distinct-identities silently collapses to 2).
- **rco-1 — *re-back* an existing builder:** keep exactly two build identities but back
  `codex-tools-1` with a **non-codex** model (Grok-superheavy / a separate-quota Claude),
  leaving lead on codex. Surgical, **no charter amendment** (precedent: the lead→Claude-4.8
  swap on 2026-06-09), deployable today; strengthens independence (resource- *and*
  identity-decorrelated) without changing any slot count.
- **fable recommendation — sequence, don't choose:** **re-back now (P0)** to unblock the
  live wedge with the smallest/safest change; **widen eligibility later** as the Claude/Grok
  pool grows, with the one-slot guard. Both honor the same invariant ⤵.

> **Guiding invariant (rco-2):** *no mandatory-AND pair of signers may share a failure
> domain (pool / provider / session-host).* Generalizes beyond this incident.

## 5. Unified plan (priority order; each preserves fail-closed)

### P0 — Decorrelate the build pair onto different pools *(today; unblocks the wedge)*
**Stand up a non-codex builder identity in the tools slot:** re-back the `codex-tools-1`
*builder identity* with a non-codex model (Grok-superheavy / separate-quota Claude), exactly
as the 2026-06-09 lead→Claude-4.8 swap did, leaving lead on codex. Build stays
`lead(codex) + tools(non-codex)` — two distinct identities in two distinct pools.
**Correction (rco-1):** enabling the Disabled Grok *aux* tasks (EF8) is a SEPARATE capacity
item — none of them is a `build_consensus` signer, so it does NOT by itself fill the build slot.
*Fail-closed:* slot count, independence, author≠reviewer, the absolute RCO veto, and the
denylist/off-allowlist → operator escalation are ALL unchanged; only *what backs* a slot changes.
**Mandatory trial (rco-2 caveat):** the gate checks only identity+head+status, so it cannot tell
a genuine build review from a rubber-stamp — the residual P0 risk is review QUALITY, not identity.
Therefore **both RCOs MUST cross-check the first several re-backed build passes** (verify the
re-backed builder actually ran the affected tests + inspected the diff at the exact head) before
that backer is trusted on gate-critical / denylisted PRs.

### P0b — Stop the live bleed + cross-pool failover *(today; sequence ALONGSIDE P0)*
- **#1, highest-ROI, one flag — switch the consumer loops to `-WakeOnly` (rco-1 EF10):** the
  script already supports it and `Watch-Bridge.ps1` writes the wake sentinel on real targeted
  traffic, so the loop wakes on demand instead of a full codex tick every ~60s forever. This
  attacks pool-exhaustion AND the manufactured-work recursion at once, and **must run alongside
  P0** (a 60s-forever cadence drains whatever pool backs it). Add a **single-in-flight guard**
  so a long (≤900s) tick can't stack concurrent codex runs (EF11). *(pure reduction of
  speculative runs; sentinel-driven traffic still wakes codex; no gate change)*
- **Backoff safety net (rco-2 EF1):** make `Start-AgentBridgeConsumerLoop.ps1` inspect the tick
  exit code; on the usage-limit signature, **back off until the reset timestamp the error names**
  ("try again at <ts>") and emit ONE status, not a failed tick every ~64s (also ends the
  ~1555-event log-spam).
- **Cross-pool failover (rco-2 P6):** generalize the dormant `lead_stall_failover` (#1089) from
  `lead→tools` (intra-codex, useless for whole-pool exhaustion) to **codex→Claude**; detect
  whole-pool exhaustion and emit ONE "pool X exhausted until <reset>; cross-pool failover
  engaged" status instead of silent wedging.
- **Driver hygiene (EF9, latent — NOT the wedge):** the live `-Apply` driver is fine; retire/
  realign the stale scheduled no-Apply duplicate, add `-LeadCosignWaiver` to the live driver
  (operator standing-auth exists), and add an auto-restart/liveness trigger. Lower priority than
  P0/`-WakeOnly` since it is not the active-wedge cause.

### P1 — Asymmetric operator-reduction: per-POLICY sign for a proven-safe class *(this week)*
Define a **mechanically-verifiable zero-blast-radius class** (read-only metrics emission;
additive default-OFF counters — exactly the #1364/#1369 shape) that auto-merges on full
consensus **without a per-PR operator signature**, gated by a checker that PROVES:
no `claim_safe` flip, no default-emission change, no authority-flag change, no routing/behavior
change, **and read-only or default-OFF** (not new throwable code in the live hot path — the
#1364 caveat). The operator signs the **invariant once** (auditable); the gate enforces it
fail-closed. *(rco-1 P1a / rco-2 P4 / fable risk-tier)*
*Fail-closed:* anything touching a Rule-10 surface, runtime/authority wiring, or denylisted
gate-classifier **logic** falls OUT of the class to explicit per-PR operator sign; ambiguity →
operator. Scope conservatively first, widen as the checker matures.

### P1b — Activate idle Claude capacity as a PRODUCER lane *(this week)*
Promote the Claude pool (rco-1/rco-2/fable) to **author** measurement-only / test / docs /
honest-rescope PRs for the safe class; codex shifts toward review/build on those PRs.
*(rco-1 P1b / rco-2 P2)* *Fail-closed:* authorship grants no gate authority; same
build+RCO+sign gate; an authoring agent cannot review its own PR (author≠reviewer already
enforced).

### P2 — Kill the waste at the source
- **Stop redundant local full-suite runs (EF6/D6):** CI is the authoritative full-suite gate;
  run only **affected** tests locally; amend `AGENTS.md`/the tools-review runbook to say so.
- **Gate the auto-spawn loops (EF2/EF5/EF7/D8):** run the cheap deterministic pre-check
  (`tools/bridge_next_action.py` / `agent_next_task.py`, plain Python, no LLM) FIRST each
  tick and only invoke a full agent when there is **actionable work**; add a burn-budget
  governor that defers low-value codex work when projected to exhaust before reset.
  *(rco-2 EF2 + P3)*
- **Tier reasoning effort (EF3):** low/medium for routine polling/simple slices; reserve
  xhigh for genuinely hard implementation/review.
- **Default-deny net-new gate-tooling** unless the PR cites a reproduced fail-open/phantom
  block OR a named product capability it unblocks → stops the recursion. *(rco-1 P2)*
- **Generalize the structured-event gate taxonomy (D5):** the entire gate reads authority
  from structured fields (`event_type`, `decision_status`, `head_sha`, `author_uuid`),
  never free-text names; only authoritative types can veto; message/handoff never can;
  blocks are head-bound and **auto-expire / require explicit retraction** (kills stale
  self-blocks); negation-aware. #1368 is instance 1 — make the single-shared-taxonomy +
  cross-consumer-consistency-test pattern mandatory. *(rco-1 P2 / rco-2 P5)*

### P3 — Cut churn
- **Content-identical-rebase carry-forward:** if `patch-id(diff-vs-base)` is unchanged,
  carry build+RCO approvals forward; re-run CI only (catches semantic skew). *(fable / rco-1 P3 / rco-2 P5)*
- **Right-size dual-RCO (D7):** full dual only for denylist/gate-critical/author-is-RCO/
  fail-open-prone; routine safe-class = one RCO pass + a non-blocking async second-RCO
  spot-check → frees ~half the RCO cycles for the producer lane. *(rco-1 P2)*
- **Guarded worktree prune (EF4):** remove per-task worktrees whose task is merged AND whose
  `rev-list HEAD ^origin/main == 0` with a clean status (read-only proof); never touch active
  ones. *(rco-2 EF4)*

### P4 — Safety substrate (longest-term; unlocks deeper operator-reduction)
Build the **auto-rollback + post-merge canary + matured synthetic adversarial corpus**
(CLAUDE.md's named preconditions for loosening Rule-10). This shifts reversible changes from
prevention-first to **fast-recovery-first (optimize MTTR, not MTBF)** and is the key that
eventually lets even off-allowlist changes flow autonomously with a rollback safety net.
*(fable)*

## 6. Safety invariants — NON-loosening (kept across ALL of the above)
- Three distinct independent **verified** identities; **author ≠ reviewer**;
  **one slot per identity** (P0/widen change eligibility/backing only, never slot count).
- **RCO veto absolute and per-identity**; a veto outranks a pass.
- **Head-exact binding**; a content-changing re-push invalidates approvals (P3 carries forward
  ONLY a mechanically-proven content-identical rebase; CI always re-runs).
- **Denylisted gate-classifier logic** and the **Rule-10 Stage-2 atomic-flip** remain hard
  operator-gated, NEVER autonomous (P1/P4 explicitly exclude them).
- **Silence/ambiguity BLOCKS**, never default-allows.

## 7. Sequencing & ownership
1. **P0 + EF1-backoff + P0b** — *now*: unblock the wedge + stop the live pool bleed.
   (ops/operator: re-back tools / enable Grok / fix the consumer loop + driver.)
2. **P1 + P1b** — this week: the biggest operator-dependence win + activate idle capacity.
3. **P2** — stop the waste (mix: AGENTS.md + runbook + loop pre-check + taxonomy; some
   fable-doable allowlist PRs, some ops).
4. **P3** — churn reduction. **P4** — substrate (enables the deeper operator-out-of-loop).

The highest-leverage moves **remove structural coupling** (P0 pool, P1 operator-gate) rather
than **add tooling** — adding tooling is what produced the waste (rco-1's meta-warning).
Criteria 1 (faster) and 2 (quality) are **co-satisfied, not traded**: speed comes from
decorrelating + parallelizing across idle capacity, not from cutting review. Criterion 3 is
pursued **asymmetrically**: automate the safe long tail aggressively, keep Rule-10/gate-logic
manual.

## 8. Status & open items
- **All five analyses are folded in** (3 structural + 3 empirical: fable-5, claude-rco-2,
  claude-rco-1). Both RCOs content+safety-reviewed this RFC → PASS (rco-2 + rco-1, each
  independently); formal `rco_pass` pending CI-green + a live merge path (the very wedge this
  RFC addresses).
- **For the operator:** the §4 re-back-vs-widen decision + per-item owner assignment.
- This RFC touches no runtime/gate code; it is `docs/architecture/` only (charter-clean) and
  PARKS for consensus/operator while the codex pool is exhausted.
