================================================================================
R20 — EXPLOSIVE MEASURED CAPABILITY GROWTH
AND RUNTIME LLM AUGMENTATION
2026-05-09
================================================================================

Recipient: whichever agent reads this first — Codex or Claude.

You are not alone in this task. This is a multi-agent meta-round that belongs to
both Codex and Claude. The first agent to receive this prompt MUST route it to
the other agent before doing substantive work.

Terminology note:
This prompt uses “LLM doping” as an internal shorthand for runtime LLM
augmentation. Do not turn that into marketing language. The engineering goal is
measured, bounded, fallback-safe, privacy-preserving runtime LLM assistance only
where it demonstrably improves quality.

================================================================================
ROUTING — DO THIS FIRST
================================================================================

Before doing anything else:

1. Save this full prompt verbatim to:

   iterations/codex_scout_tasks/r20_master_prompt_2026_05_09.md

2. Compute its SHA-256 hash.

3. Send a bridge event to the other agent.

If you are Claude, send to Codex.
If you are Codex, send to Claude.

Use:

powershell
& .\.agent-bridge\bin\Write-AgentEvent.ps1 `
    -Agent operator `
    -To <other-agent> `
    -Type decision `
    -Status proposed `
    -Severity major `
    -TaskId r20-explosive-intelligence-growth-doping-2026-05-09 `
    -Message "R20 master prompt is ready. Read: iterations/codex_scout_tasks/r20_master_prompt_2026_05_09.md ; sha256=<PROMPT_SHA256>. Do not start implementation until both agents have written their response files and r20_synthesis_2026_05_09.md exists."

Replace <other-agent> with `claude` or `codex`.

4. Confirm through the bridge that the other agent has received or acknowledged
   the prompt:

powershell
& .\.agent-bridge\bin\Read-AgentBridge.ps1 -Agent <your-agent-name> -ShowEvents -Tail 20

5. If no acknowledgement appears within five minutes, resend the bridge event
   once.

6. If the bridge is unavailable, write:

   iterations/codex_scout_tasks/r20_bridge_blocker_<your-agent>_2026_05_09.md

   Then stop. Do not implement R20 work without the bridge.

7. Do NOT claim any R20.X implementation PR before the synthesis phase is done.

The synthesis phase is complete only when:

- Claude has written:
  iterations/codex_scout_tasks/r20_explosive_growth_response_claude_2026_05_09.md

- Codex has written:
  iterations/codex_scout_tasks/r20_explosive_growth_response_codex_2026_05_09.md

- Both agents have read each other’s response.

- A shared synthesis file exists:
  iterations/codex_scout_tasks/r20_synthesis_2026_05_09.md

- The synthesis file clearly assigns PR ownership, review ownership, and the
  minimum viable overnight scope.

Only after synthesis may implementation begin.

================================================================================
GLOBAL RULES
================================================================================

General:

1. Do not ask the operator for clarification unless a hard safety blocker exists.
2. Do not create a monolithic PR.
3. Keep one owner per PR.
4. Every PR must include a measurable result or an explicit “no measurable
   improvement, abandoned” result.
5. Every PR must preserve existing gates.
6. Do not weaken bridge, orchestrator, claim, or token hygiene.
7. Do not print secrets, tokens, credentials, or full environments.
8. Do not call `gh auth token`.
9. Do not call `gh auth git-credential get`.
10. Do not embed tokens in URLs.
11. Push only with plain `git push` if needed. If credentials fail, stop.
12. No hidden runtime LLM calls. All runtime LLM calls must go through
    BridgeLLMClient.
13. No cloud LLM call without privacy redaction enabled by default.
14. No dependency on a single cloud vendor.
15. Profile S must work without internet and without LLM runtime.
16. Existing tests must not break.
17. If a proposed LLM augmentation does not improve quality by the required
    threshold, remove it or leave it disabled with clear evidence.
18. Do not claim “AGI”, “consciousness”, “world fastest”, “beats all competitors”,
    or raw-intelligence superiority.
19. Do not create marketing claims unsupported by measurements.
20. Do not start R20.6 release/publish until R20.1–R20.5 status is known.

PR discipline:

- Target: one PR per 30–90 minutes.
- One measurable improvement per PR.
- Codex and Claude must alternate owner/reviewer roles.
- The non-owner agent must do pre-merge review and post-merge audit.
- Use bridge claim system before editing.
- Use bridge events for PR status.

Time budget:

- 10-hour overnight work window.
- At 5 hours, send one bridge event to operator with a one-line status per PR.
- If all six PRs cannot be completed, define and execute a minimum viable scope.

Minimum viable scope recommendation:

- Must include R20.1.
- Must include R20.2 skeleton/prototype if feasible.
- Must include R20.5 if R16 process isolation is still blocking review quality.
- R20.3 may be deferred if no safe/valuable doping point is found.
- R20.4 may be deferred if deployment profiles require too much product-level
  refactor.
- R20.6 may be a “release-readiness doc only” if not all implementation PRs land.

================================================================================
PART 0 — BASELINE CONFIRMATION
================================================================================

Both agents must answer this before proposing implementation.

Confirm that both agents agree on the current baseline.

Known baseline to verify:

- R10–R12 test coverage + bridge foundation:
  PR #154–#161 merged.

- R13 runtime-root + R13.5 BOOTSTRAP one-command:
  PR #162 merged, gate 1 verified.

- R15 stale-claim-lease auto-release:
  PR #163 merged, gate 3 verified.

- Phase D round 1:
  MAGMA microbenchmark + repeatable script, PR #164 merged.

- Phase D round 2 / R17:
  single-pass ranking refactor merged.
  Measured improvement: 22.97 ms → 1.23 ms (~33x), with some measurements
  reporting 0.69 ms.

- Phase D round 3 candidate:
  vector_events checkpoint/offset reader.
  Known measurement candidate: 115 ms / 10k events.
  Not yet implemented unless later evidence says otherwise.

- R16 architect/security/reliability as separate processes:
  documented in PR #163 commit, not yet implemented unless later evidence says
  otherwise.

Each agent must list:

1. All measured bottlenecks already known:
   - number
   - file
   - source session / Y-round / PR / output file

2. All queued candidates without measurements.

3. Metrics missing entirely, for example:
   - end-to-end pipeline latency
   - quality score
   - per-agent cycle time
   - per-PR improvement rate
   - review false-positive / false-negative rate
   - cloud/local LLM cost per useful improvement

Write your baseline answer to:

- Claude:
  iterations/codex_scout_tasks/r20_explosive_growth_response_claude_2026_05_09.md

- Codex:
  iterations/codex_scout_tasks/r20_explosive_growth_response_codex_2026_05_09.md

Use sections:

- Part 0 — Baseline
- Known measured bottlenecks
- Unmeasured candidates
- Missing metrics
- Baseline confidence
- Evidence references

================================================================================
PART 1 — DEFINE EXPLOSIVE MEASURED CAPABILITY GROWTH
================================================================================

Define “explosive local intelligence/capability growth” as compound measured
improvement along three axes.

Every R20 PR must measure or update all three axes, even if B and C are still
coarse at first.

--------------------------------------------------------------------------------
Axis A — Per-operation latency
--------------------------------------------------------------------------------

Metric:

- microseconds/op or milliseconds/op
- p50
- p95
- p99
- same snapshot / same dataset

Example:

- ms_ranking 22.97 ms → 1.23 ms

Each agent must answer:

1. What is the current measurement chain?
2. Where are results recorded?
3. Which workloads currently have p50/p95/p99?
4. Which workloads only have average or single-run measurements?
5. Which hot paths need repeatable scripts?

--------------------------------------------------------------------------------
Axis B — Per-decision quality
--------------------------------------------------------------------------------

New metric.

Metric examples:

- task-specific quality score
- A/B vs baseline
- “top-5 ranking judged reasonable by operator”
- precision/recall if labeled data exists
- pass/fail correctness if deterministic expected answers exist
- review usefulness score if reviewing iteration packages

Each agent must answer:

1. Does WaggleDance currently have any quality score or labeled dataset?
2. If yes, where?
3. If no, how can we collect one without slowing production?
4. Which first R20 PR can introduce a minimal quality-score mechanism?
5. How do we prevent subjective metrics from becoming fake precision?

Hard requirement:

- R20.3 LLM augmentation must be A/B tested with a quality score.
- If treatment does not improve quality by at least 20% over heuristic baseline,
  remove or disable the LLM augmentation.

--------------------------------------------------------------------------------
Axis C — Cumulative learning / improvement velocity
--------------------------------------------------------------------------------

New metric.

Metric:

- measurable improvements per hour
- PR cycle time
- improvement magnitude per agent-hour
- regression if velocity slows
- compound improvement rate

Each agent must answer:

1. How many measured improvements have landed so far?
2. What is the trend per session?
3. Does iterations/codex_scout_tasks/ already contain session meta-metrics?
4. If not, where should EVOLUTION_INDEX.md live?
5. What is the minimal schema for an evolution index entry?

Every R20 PR must update or at least preserve:

  iterations/EVOLUTION_INDEX.md

If no improvement is measured, the entry must say:

  no measurable improvement; abandoned or deferred

================================================================================
PART 2 — RUNTIME LLM AUGMENTATION (“LLM DOPING”)
================================================================================

So far, Claude and Codex have mostly been used at development time:
scout, implement, review, merge.

The next layer of compound growth is runtime LLM augmentation inside the solver:
a hot or warm runtime decision point may call a local or cloud LLM when the
existing heuristic cannot make a high-quality decision.

This must be disciplined, measured, budgeted, privacy-preserving, and removable.

================================================================================
2.1 — Find candidate injection points
================================================================================

Each agent must list 5–10 concrete WaggleDance code points.

For each point:

1. File + function + line number.
2. What decision is made?
3. How often is this path executed?
   Examples:
   - 10/min
   - 1,000/sec
   - startup only
   - PR/review only
4. Current heuristic.
5. Where the heuristic fails.
6. Latency budget.
   Examples:
   - 20 ms
   - 200 ms
   - 2 seconds
7. Could a local quantized model be enough?
8. Could semantic cache be enough without LLM?
9. What quality metric would prove improvement?
10. Is this a good first R20.3 point?

Do not implement before synthesis chooses one first doping point.

================================================================================
2.2 — Required fallback chain
================================================================================

Every runtime LLM augmentation point must follow this fallback chain:

1. Cache hit:
   - semantic cache
   - nearest embedding neighbor
   - exact cache
   - previous decision replay

2. Local LLM:
   - quantized model
   - e.g. Llama 3.2 3B Q4 or similar
   - target latency roughly 50–200 ms if hardware allows

3. Cloud LLM:
   - Anthropic
   - OpenAI
   - Azure OpenAI
   - Vertex AI
   - Cohere
   - Groq
   - Together AI
   - other provider plugin

4. Pure heuristic:
   - current code
   - no LLM
   - must work offline

Explosive growth must not break when:

- network goes down
- cloud key expires
- cloud model is unavailable
- local model is missing
- compute is limited
- daily budget is exhausted

================================================================================
2.3 — BridgeLLMClient component
================================================================================

Design a concrete component:

  BridgeLLMClient

It must read:

  AGENT_BRIDGE_RUNTIME_ROOT/llm_config.json

It must support provider plugins:

- cache
- local-ollama
- local-llamacpp
- anthropic-api
- openai-api
- azure-openai
- vertex-ai
- cohere
- groq
- together-ai
- local-vllm
- huggingface-tgi
- heuristic-fallback

R20.2 prototype may implement only:

- cache placeholder
- local-ollama provider
- heuristic fallback

But the interface must be provider-extensible.

Every call must log:

- call_id
- timestamp
- injection_point
- provider
- model
- prompt_hash
- prompt_redaction_status
- latency_ms
- tokens_in
- tokens_out
- fallback_level
- success
- error_class if failed
- cost_cents if known
- budget_state

Each call must support per-call budgets:

- max_latency_ms
- max_cost_cents
- max_retries
- allow_cloud
- allow_pii_to_cloud
- require_json
- fallback_policy

Pre-warm / speculative mode:

- A component may request a speculative call before the final decision is known.
- Speculative calls must be budgeted and tagged:
  speculative=true

================================================================================
2.4 — A/B value metrics
================================================================================

Every augmentation point must be tested first in A/B mode:

- A: heuristic-only control
- B: LLM-augmented treatment

Run 1000+ calls if feasible, or a smaller documented initial sample if not.

Measure:

- quality_score_control
- quality_score_treatment
- delta_quality
- latency_control_p50/p95/p99
- latency_treatment_p50/p95/p99
- cost_treatment
- fallback_distribution
- failure_rate
- cache_hit_rate

Rule:

If LLM treatment does not improve quality score by at least 20% over the
heuristic baseline, remove it, keep it disabled, or write a Decision B for that
doping point.

No “maybe useful later” hot-path LLM calls.

================================================================================
2.5 — Cost awareness
================================================================================

Add or design:

  llm_budget.json

Fields:

- max_calls_per_day
- max_cents_per_day
- max_calls_per_injection_point
- max_cents_per_injection_point
- degradation_strategy_when_budget_low:
  - local-only
  - cache-only
  - heuristic-only
  - error
- telemetry_path
- alert_thresholds

Runtime telemetry must show:

- remaining budget
- current mode
- calls/day
- cost/day
- degradation status

================================================================================
2.6 — Privacy-preserving LLM augmentation
================================================================================

Design:

- BridgeLLMRedactor
- BridgeLLMRehydrator

Before any prompt goes to a cloud provider:

- detect and replace PII / secrets:
  - email addresses
  - phone numbers
  - credit card numbers
  - access tokens
  - API keys
  - private keys
  - user identity markers
  - local file paths if configured sensitive

Use placeholders:

- <EMAIL_1>
- <PHONE_1>
- <TOKEN_1>
- <PATH_1>

Rehydrate only where safe.

Cloud PII redaction must be ON by default.

It may be bypassed only with an explicit per-call flag:

  AcceptPiiToCloud = true

That bypass must log a warning and require the call to be auditable.

================================================================================
PART 3 — RECURSIVE SELF-IMPROVEMENT
================================================================================

The explosive growth mechanism is recursive self-improvement, not one-off
optimization.

Design an R-Self-Imp-N loop that:

1. Reads previous session metrics:
   - iterations/
   - codex_scout_tasks/
   - bridge events
   - EVOLUTION_INDEX.md
   - PR results
   - benchmark files

2. Identifies three systematic patterns:
   - where improvements were largest
   - where attempts failed
   - which metrics were missing

3. Generates 5–10 new scout candidates qualitatively different from prior work:
   - architecture changes
   - data structure changes
   - async/concurrency
   - distributed processing
   - indexing
   - caching
   - better measurement
   - runtime augmentation
   - deployment hardening

4. Reviews the prompts used by Codex and Claude:
   - which prompt structures produced useful outputs?
   - which produced vague outputs?
   - which produced wrong or unsafe outputs?

5. Updates review prompts if evidence supports the change:
   - prompts/review/architect.md
   - prompts/review/security.md
   - prompts/review/reliability.md

6. Improves the scout→implement→review→merge loop based on evidence.

Constraints:

- Do not break existing tests.
- After every self-improvement cycle, run the Phase C gate verification.
- Every self-improvement PR must be tagged in metadata as:
  r-self-imp-N
- Every self-improvement PR requires review by both agents:
  - one owner
  - the other reviews
  - if owner is Claude, Codex reviews
  - if owner is Codex, Claude reviews

================================================================================
3.1 — Self-improvement metrics
================================================================================

Track:

- cycle_time_minutes
- quality_multiplier
- measurable_improvement_per_agent_hour
- diversity_score
- failure_rate
- abandoned_candidate_count
- compound_rate

Example:

If ten cycles each produce 1.5x improvement, theoretical compound rate is 57.7x.
Track the real number instead of claiming theoretical growth.

================================================================================
PART 4 — ENVIRONMENT-INDEPENDENT DEPLOYMENT
================================================================================

The solver must run in environments where Claude Code and Codex are not running.

Users may have:

- Windows
- Linux
- macOS
- ARM
- x86
- GPU
- no GPU
- internet
- no internet
- cloud credentials
- no cloud credentials

Same build, different config. No separate codebase per profile.

================================================================================
4.1 — Deployment profiles
================================================================================

Define three profiles.

--------------------------------------------------------------------------------
Profile S — Small
--------------------------------------------------------------------------------

Environment:

- no GPU
- no internet
- limited compute

Behavior:

- pure heuristic
- cache if available
- no runtime LLM augmentation
- fallback level 4 only
- local telemetry stored for later analysis

Example:

- field measurement unit
- offline industrial device

--------------------------------------------------------------------------------
Profile M — Medium
--------------------------------------------------------------------------------

Environment:

- local GPU or CPU capable of small quantized model
- optional local model server
- no required internet

Behavior:

- cache
- local LLM
- heuristic fallback
- no cloud by default

Example:

- workstation
- edge server
- local factory node

--------------------------------------------------------------------------------
Profile L — Large
--------------------------------------------------------------------------------

Environment:

- full setup
- internet
- optional cloud LLM keys
- local LLM optional

Behavior:

- all four fallback levels
- cache
- local LLM
- cloud LLM
- heuristic fallback
- speculative prefetch allowed
- A/B testing enabled

Example:

- central solver fleet
- cloud-connected deployment

Requirement:

Only config changes between profiles. Same build.

================================================================================
4.2 — Bootstrap without Claude Code
================================================================================

Design:

  Start-WaggleDanceSolver.ps1

It must:

1. Read AGENT_BRIDGE_RUNTIME_ROOT.
2. Load solver profile.
3. Start solver runtime.
4. Use local LLM if configured and available.
5. Fall back to heuristic if LLM unavailable.
6. Record telemetry.
7. Continue collecting data even when Claude/Codex are not running.
8. Make telemetry available for Claude/Codex later.

Production use must not require Claude Code or Codex.

================================================================================
4.3 — Production update cycle
================================================================================

Design a production update cycle:

Version naming example:

  solver-v1.2.3-magma1.23ms-vector115ms

Include:

- auto-update vs opt-in
- rollback
- canary
- 5% traffic to new version
- promote to 100% only if metrics are green
- rollback if new version is slower or lower quality
- benchmark threshold
- smoke test
- operator-visible release notes

================================================================================
PART 5 — CONCRETE R20 DELIVERABLES
================================================================================

R20 has six PRs.

Do not merge a PR without measurements.

================================================================================
R20.1 — Evidence Matrix Meta-Metric
================================================================================

Owner recommendation: Claude.

Goal:

Add:

  iterations/EVOLUTION_INDEX.md

Purpose:

Track per-round baseline, improvement, quality, cycle time, and cumulative
growth.

Must include:

- session_id
- PR number
- owner agent
- reviewer agent
- baseline metric
- after metric
- improvement factor
- quality metric
- cycle time
- failed attempts
- lessons learned
- next bottleneck

Must support Axes A/B/C:

- A latency
- B quality
- C cumulative learning velocity

This is the foundation for Axis C.

================================================================================
R20.2 — BridgeLLMClient Prototype
================================================================================

Owner recommendation: Codex.

Goal:

Implement BridgeLLMClient prototype with four-tier fallback design.

Initial implementation must include:

- interface
- config loader
- telemetry
- local-ollama provider if available
- heuristic fallback
- cache placeholder or simple exact cache
- budget config stub
- provider plugin interface
- test coverage

Do not require cloud credentials.

Do not break Profile S.

================================================================================
R20.3 — First Runtime LLM Augmentation Point
================================================================================

Owner recommendation: Codex.

Goal:

Choose one code point where LLM augmentation is likely useful.

Process:

1. Scout 5–10 candidate points.
2. Score them by:
   - expected quality gain
   - latency budget
   - implementation risk
   - fallback feasibility
   - measurement feasibility
3. Select exactly one.
4. Implement A/B mode.
5. Run quality test.
6. If quality gain <20%, remove/disable and document Decision B for that point.
7. If quality gain >=20%, keep behind config flag.

Hard requirements:

- all LLM calls through BridgeLLMClient
- no hidden calls
- Profile S still works
- privacy redaction for cloud path
- telemetry

================================================================================
R20.4 — Profile S/M/L Deployment Config
================================================================================

Owner recommendation: Claude.

Goal:

Add:

  solver-profiles/small.json
  solver-profiles/medium.json
  solver-profiles/large.json
  Start-WaggleDanceSolver.ps1

The same codebase must support all profiles by config only.

Docs:

- docs/deployment/profile-small.md
- docs/deployment/profile-medium.md
- docs/deployment/profile-large.md
- docs/deployment/profile-selection.md

Tests:

- small profile starts without internet or LLM
- medium profile degrades if local LLM missing
- large profile degrades if cloud keys missing
- no profile prints secrets
- telemetry is written

================================================================================
R20.5 — R16 Architect/Security/Reliability Process Isolation
================================================================================

Owner recommendation: Claude.

Goal:

Make architect/security/reliability review processes truly separate.

Current problem:

If all “perspectives” are produced in one pass, correlated failure modes remain.
R16 requires independent processes.

Deliverables:

- Invoke-WaggleReview.ps1 runs separate processes for:
  - architect
  - security
  - reliability
- outputs are merged only by a synthesis pass
- each process has separate transcript/output
- no shared mutable review state
- each review can fail independently
- synthesis identifies agreement/disagreement

Add:

  orchestrator\Invoke-WaggleReviewSynthesis.ps1

or equivalent.

Tests:

- architect-only run
- security-only run
- reliability-only run
- all three run separately
- synthesis reads three outputs
- missing one review fails closed or marks incomplete
- no Bash in review mode

================================================================================
R20.6 — Release and Publish
================================================================================

Owner recommendation: Codex.

Run only after R20.1–R20.5 status is known.

Goal:

Prepare a release if enough real implementation landed.

If R20.3 adds a new API surface or production runtime feature:

- propose semver bump
- update CHANGELOG.md
- update README.md
- update docs
- generate release notes from bridge events + EVOLUTION_INDEX.md
- include at least one before/after graph
- add migration guide

README front page should include, if measured:

- Current measured MAGMA latency
- Previous measured MAGMA latency
- Deployment profiles S/M/L
- Optional LLM augmentation fallback chain
- Current EVOLUTION_INDEX summary

Docker targets:

- waggledance:latest
- waggledance:small
- waggledance:medium

If GHCR is configured:

- ghcr.io/<org>/waggledance:vX.Y.Z

Smoke-test PR:

- docker pull
- docker run
- wait for ready
- run baseline benchmark
- fail if image does not start
- fail if latency >2x expected threshold

If not enough implementation landed, R20.6 may instead be a release-readiness
Decision B document, with exact blockers.

================================================================================
PART 6 — REQUIRED RESPONSE FILES AND SYNTHESIS
================================================================================

Before implementation, both agents write:

Codex:

  iterations/codex_scout_tasks/r20_explosive_growth_response_codex_2026_05_09.md

Claude:

  iterations/codex_scout_tasks/r20_explosive_growth_response_claude_2026_05_09.md

Each response must have sections:

1. Part 0 — Baseline
2. Part 1 — Growth axes A/B/C
3. Part 2 — Runtime LLM augmentation
4. Part 3 — Recursive self-improvement
5. Part 4 — Deployment profiles
6. Part 5 — R20 deliverables
7. Open questions
8. Recommended PR order
9. Proposed ownership
10. Risk register
11. Minimum viable overnight scope

Then:

1. Each agent reads the other’s response.
2. Each marks:
   - agreements
   - disagreements
   - reasons
3. Both write a shared synthesis:

   iterations/codex_scout_tasks/r20_synthesis_2026_05_09.md

The synthesis must include:

- final PR order
- owner per PR
- reviewer per PR
- minimum viable scope
- which PRs may be deferred
- exact first PR to implement
- gates per PR
- measurement plan
- bridge claim plan
- 5-hour checkpoint format
- morning summary format

Implementation may start only after synthesis exists.

================================================================================
PART 7 — ANTI-SOLUTIONS
================================================================================

Do NOT do these:

1. “Use AI for everything.”
   If heuristic is better, use heuristic.

2. Monolithic PR that implements all R20 at once.

3. Secret dependency on a specific cloud API.

4. Runtime LLM call that bypasses BridgeLLMClient.

5. Cloud LLM call without redaction by default.

6. “Looks good but not measured” PR.

7. Hidden hot-path LLM calls.

8. Profile S breaking because LLM is missing.

9. Browser automation.

10. Waiting for a perfect prompt before measuring.

11. Provider-specific lock-in.

12. Unbounded cloud cost.

13. Unbounded latency.

14. Claims of intelligence growth without Axes A/B/C.

15. A release PR before the implementation PRs are reviewed.

================================================================================
PART 8 — INITIAL WORK SPLIT
================================================================================

Suggested ownership:

Codex leads:

- R20.2 BridgeLLMClient
- R20.3 first doping point
- R20.6 release/docs/Docker

Claude leads:

- R20.1 EVOLUTION_INDEX / meta-metric
- R20.4 deployment profiles
- R20.5 R16 process isolation

Rationale:

Codex has been stronger in systematic scouting and tooling design:
- PR #157 mentor fact_id
- PR #155 PropertyNotFoundStrict
- PR #163 fix-on-branch

Claude has been stronger in infrastructure components and meta-metrics:
- R13.5 BOOTSTRAP
- R15 stale lease
- gate verification
- EVOLUTION_INDEX-style reasoning
- honesty around “architect/security/reliability theater”

If either agent disagrees with the split, it must write the alternative and
reason in r20_synthesis_2026_05_09.md before implementation starts.

Per PR:

- one owner
- other agent pre-merge review
- other agent post-merge audit
- bridge claim required
- one measurable improvement required

================================================================================
MIDPOINT SUMMARY
================================================================================

At the 5-hour mark, send one operator bridge event:

powershell
& .\.agent-bridge\bin\Write-AgentEvent.ps1 `
  -Agent <your-agent-name> `
  -To operator `
  -Type decision `
  -Status reported `
  -Severity major `
  -TaskId r20-explosive-intelligence-growth-doping-2026-05-09 `
  -Message "R20 midpoint: R20.1=<status>; R20.2=<status>; R20.3=<status>; R20.4=<status>; R20.5=<status>; R20.6=<status>; blockers=<short>; metrics=<short>"

================================================================================
MORNING SUMMARY
================================================================================

At the end of the overnight window, send one operator bridge event:

powershell
& .\.agent-bridge\bin\Write-AgentEvent.ps1 `
  -Agent <your-agent-name> `
  -To operator `
  -Type decision `
  -Status reported `
  -Severity major `
  -TaskId r20-explosive-intelligence-growth-doping-2026-05-09 `
  -Message "R20 morning summary: (1) PR numbers + one-line description each; (2) all new before/after metrics; (3) current EVOLUTION_INDEX.md state; (4) top 3 next bottlenecks for R21."

Also write:

  iterations/codex_scout_tasks/r20_morning_summary_2026_05_10.md

================================================================================
START CONDITION
================================================================================

Begin now.

First action: ROUTING.

Second action: Part 0 baseline response.

Do not implement until both responses and synthesis exist.