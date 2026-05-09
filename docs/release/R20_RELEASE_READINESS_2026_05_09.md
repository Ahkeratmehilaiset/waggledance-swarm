# R20.6 — Release-readiness Decision B (2026-05-09)

- date: 2026-05-09
- author: claude (resilience-takeover; R20.6 was Codex-owned per
  `iterations/codex_scout_tasks/r20_synthesis_2026_05_09.md`)
- status: NO RELEASE THIS SESSION — Decision B per R20 master prompt rule
  20 ("Do not start R20.6 release/publish until R20.1–R20.5 status is
  known") and the prompt's explicit "release-readiness Decision B
  document" alternative when "not enough implementation landed" or
  when ship-readiness needs more soak.

## What landed in this session

R17 / R18 / R19 (Phase D scaling pass) and R20 (explosive-growth
substrate) — see `CHANGELOG.md` 2026-05-09 entry for the full
decomposition. Five R20 implementation PRs landed plus the routing /
synthesis docs PRs and one abandoned-with-reason candidate (R18 Cand 2).

Cumulative measured wins on Phase D:

| Operation | Before | After | Speedup |
|---|---:|---:|---:|
| TrustAdapter.get_ranking 512 | 22.97 ms | 0.86 ms | ~26.7× |
| vector_events incr 100 vs full 10k | 108.84 ms | 1.60 ms | ~68× |
| EventLogAdapter.log_event 5000 | 81.41 ms | 25.49 ms | ~3.2× |
| HexTopologyRegistry.get_neighbor_cells 20k | 199.29 ms | 21.78 ms | ~9.1× |
| HexTopologyRegistry.select_origin_cell 2k | 41.43 ms | 21.33 ms | ~1.94× |

R20 substrate: `BridgeLLMClient` four-tier fallback + `ABHarness` A/B
substrate + `EVOLUTION_INDEX.md` meta-metric file + `Invoke-RoleReview`
process-isolated review wrapper + Profile S/M/L deployment configs.

## Why this is Decision B, not a release

Per R20 master prompt rule 4 ("Every PR must include a measurable result
or an explicit 'no measurable improvement, abandoned' result") plus
rule 14 ("Claims of intelligence growth without Axes A/B/C") plus rule
15 ("A release PR before the implementation PRs are reviewed").

Concretely:

1. **Axis B is `null` everywhere.** R20.3 ships `ABHarness` but
   defers production wire-up of any injection point because no
   labelled corpus exists for the 20% quality-gain threshold (R20
   master prompt §2.4). Until a labelled corpus arrives and at least
   one A/B is run, every entry in `iterations/EVOLUTION_INDEX.md` has
   `axis_b_quality: null`. Releasing now means claiming explosive
   growth on Axis A alone.
2. **No new runtime API surface activates.** R20.2 BridgeLLMClient
   is shipped behind `Profile S → BridgeLLMClient.disabled()` AND
   `ABHarness(treatment_share=0.0)` as the safe default. No call site
   in production code constructs a treatment LLMRequest yet. Per R20
   master prompt §2 the substrate is in place; the augmentation is
   not.
3. **Cloud providers are stubs.** Tier 3 of the four-tier fallback
   chain has the registration hook but no Anthropic / OpenAI /
   Vertex / Cohere / Groq plugin. Releasing a "cloud-LLM-capable"
   build without exercising the cloud tier would be premature.
4. **R19 Priority 3 Cand 2 + Cand 3 are deferred.** Build-phase
   transaction batching (5–20× build speedup at 10k) and lookup p99
   profiling are sized but not implemented. Releasing without them
   leaves measurable wins on the table.
5. **Codex was absent for ~3.5 hours.** Of the 11 PRs in this
   session, four were autonomously merged by Claude per CLAUDE.md
   rule 9 + the operator resilience directive. The synthesis at
   `r20_synthesis_2026_05_09.md` is single-author; a real release
   should wait until Codex re-attaches and either ratifies or amends
   the synthesis (the file reserves a `Codex amendment` block at the
   bottom for exactly this).

Rule 20 backs all of this up: "Do not start R20.6 release/publish until
R20.1–R20.5 status is known." Every R20.x has now landed at substrate
level; what's missing is the **soak** between substrate and release.

## What activates a real release

A real release tag (semver bump, GitHub release, Docker images) lands
when ALL of:

1. **An A/B has been run and recorded.** R20.3 activation criteria
   (per `iterations/codex_scout_tasks/r20_3_decision_b_2026_05_09.md`):
   any of (a) labelled `(input, ground_truth_grade)` pairs ≥100, OR
   (b) deterministic golden-output evaluator for one decision, OR (c)
   operator review channel grading ≥10 decisions/day for a week. The
   resulting `delta_quality` is recorded in `EVOLUTION_INDEX.md` and
   either crosses the 20% threshold (deploy behind config flag) OR
   is logged below threshold (per rule 17, remove or keep disabled).
2. **At least one cloud provider plugin lands.** Anthropic is the
   recommended first since the bridge already uses it elsewhere.
   Plugin must integrate `BridgeLLMRedactor` (PII redaction ON BY
   DEFAULT for cloud-bound prompts). A second R20.6 follow-up PR
   handles the redactor.
3. **R19 Cand 2 (transaction batching) is run at full 10k scale**
   and either ships with measured 5–20× build speedup or is logged
   as below-floor in `EVOLUTION_INDEX.md`. The bench takes ~150 s at
   10k; needs a quiet machine for an apples-to-apples comparison.
4. **Codex re-attaches and signs the synthesis amendment block** —
   confirming or amending the resilience-driven solo synthesis. If
   Codex ratifies the stand-in, the release proceeds with the
   synthesis as-is. If Codex amends, the release waits for the
   amended PR plan.
5. **Phase C gate verification re-runs cleanly.** Cold-shell BOOTSTRAP
   one-command + 5+ autonomous PRs + 5-min stale-claim auto-release
   per the prior gate-verification protocol. R15 / R13.5 already
   verified these on 2026-05-09 morning; a re-run on the post-R20
   commit confirms the substrate addition didn't drift the gates.

## What changes to ship a release if all five activate

When all five activation conditions hit, a follow-up R20.6 PR will:

1. Bump semver per the rule of thumb: minor bump if R20.3 added a
   user-facing API (treatment LLMRequest factory + flag), patch if
   only deferred-to-later wire-ups landed.
2. Update `CHANGELOG.md` 2026-05-09 entry with the activation
   evidence (`delta_quality`, cloud-provider PR ref, R19 Cand 2 PR
   ref, Codex ratification commit, gate re-run output).
3. Update `README.md` front-page note to reflect the activated
   capabilities (current MAGMA latency from `EVOLUTION_INDEX.md`,
   Profile S/M/L availability, fallback chain shape).
4. Build Docker targets:
   - `waggledance:latest` (Profile L compatible)
   - `waggledance:medium` (local-LLM-only)
   - `waggledance:small` (offline / heuristic-only) — explicitly
     `--network none` smoke-tested
5. Generate release notes from bridge events + `EVOLUTION_INDEX.md`
   delta since v3.10.4-incremental-gap-replay-alpha (the previous
   tag).
6. Optional (when GHCR is configured):
   `ghcr.io/<org>/waggledance:vX.Y.Z` + per-profile tags.
7. Smoke-test PR per the master prompt §R20.6:
   `docker pull` → `docker run` → wait for ready → run baseline
   benchmark → fail if image does not start → fail if latency >2×
   expected threshold.

## What this session is fine to ship right now

A docs-only release is appropriate **without** a tag bump:

- The `CHANGELOG.md` 2026-05-09 entry (this PR)
- The `README.md` front-page snapshot (this PR)
- This Decision B doc (this PR)

That captures the engineering work for posterity and future R21
sessions, but does not claim a runtime capability that hasn't been
A/B tested yet.

## Anti-patterns avoided

- Did NOT bump the version tag based on substrate-only PRs.
- Did NOT generate Docker images claiming runtime LLM capability
  before any cloud provider is integrated.
- Did NOT release "this build supports Profile L" before Profile L
  has actual cloud-tier serving.
- Did NOT cite the Phase D speedups as "intelligence growth" — they
  are Axis A speedups, no Axis B / Axis C trend-validated claim.
- Did NOT bypass the operator resilience contract: every autonomous
  merge under Codex silence is logged in the bridge with the rule 9
  guardrail evaluation.

## Pointer to the morning summary

This session is the second half of an overnight sprint. The morning
summary (per R20 master prompt) lives at
`iterations/codex_scout_tasks/r20_morning_summary_2026_05_10.md`.
That file enumerates the 11 PR numbers + before/after metrics +
current `EVOLUTION_INDEX.md` state + top-3 next bottlenecks for R21.
