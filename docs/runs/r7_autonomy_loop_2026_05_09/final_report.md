# R7 — autonomy bridge-loop round 1 (2026-05-09)

**Branch:** waggledance/r7-final-report
**Status:** ROUND-1 COMPLETE — all nine candidates merged
**Operator paste-relay events during R7:** 0 (apart from one
`miksi pysähtynyt` nudge that prompted a status report; no
mid-task corrections)

## What R7 was

R7 took a single subsystem — `waggledance/core/solver_synthesis/` —
that Claude's parallel scout had flagged during R6 as the largest
remaining direct-import test gap (10 of 12 files were direct-test-
bare). The single round closed nine of those ten in one autonomous
pass.

The autogrowth pipeline lives in this subsystem. Each file pins a
different safety boundary between LLM-generated solver candidates
and runtime promotion: deterministic compilation, syntactic +
semantic + shadow-eval verdicts, U1/U3 routing thresholds, the §U3
quota gate, the §U3 cold-solver promotion rule, the constitutional
guard requiring an explicit `human_approval_id` for final approval.

## Outcomes by PR

| PR | Squash SHA | Surface | Tests |
| --- | --- | --- | --- |
| #114 | `b8da254f` | `deterministic_solver_compiler.py` | 27 |
| #115 | `7709dcf2` | `solver_quarantine.py` | 18 |
| #116 | `d3f9f84` | `validators.py` | 22 |
| #117 | `03052782` | `cold_shadow_throttler.py` | 20 |
| #118 | `68f19c6d` | `solver_family_registry.py` | 15 |
| #119 | `cd4f0cc2` | `declarative_solver_spec.py` | 22 |
| #120 | `3f6eaf22` | `gap_to_solver_spec.py` | 10 |
| #121 | `562b5a3d` | `bulk_rule_extractor.py` | 17 |
| #122 | `311cf5c1` | `solver_candidate_store.py` | 37 |

Combined: **151 new direct-import test assertions** in `main` against
`solver_synthesis/`. Subsystem coverage went from **1/12 to 10/12**
direct-tested in a single autonomous round. The two remaining files
are `solver_bootstrap.py` (already had a sibling test before R7) and
`llm_solver_generator.py` (needs LLM mocking; deferred to a later
round).

## The load-bearing assertions

Three R7 invariants are explicitly load-bearing for safety, not just
shape:

- **`admit_to_approved` constitutional guard** (PR #115).
  `constitution.no_foundational_auto_promotion` says final approval
  MUST require an explicit `human_approval_id`. Test
  `test_admit_to_approved_REQUIRES_human_approval_id_constitutional_guard`
  asserts that without an approver id, the function refuses
  regardless of candidate state or quota — and quota does NOT
  decrement when the constitutional guard blocks. A future
  contributor cannot silently bypass the guard.
- **`decide_verdict` precedence** (PR #116). Six-step ordering:
  syntactic_invalid > semantic_invalid > regression_detected >
  needs_more_shadow > rejected_low_value > pass_all_gates.
  Pinned per gate-failure permutation; the
  `min_shadow_observations=50` boundary is asserted both just-below
  and just-at.
- **`can_exit_cold` three gates** (PR #122). `use_count ≥ 50` AND
  `shadow_observation_seconds ≥ 3600` AND `critical_regressions == 0`.
  All three gates exhaustively covered.

## What worked

- **Parallel Claude-scout cross-check.** During R6 Claude wrote a
  `solver_synthesis` file inventory to the bridge before Codex's
  round-3 scout completed, identifying the 10/12 gap. R6 round 3
  ended up landing on the smaller `dreaming/` area (also valid),
  and Claude's scout finding became R7. Two-engine scouting picks
  up areas one engine alone might miss.
- **Pure-test exception scaling.** All nine R7 PRs are pure test
  additions; none triggered the GPT consensus gate per `PROTOCOL.md`.
  Throughput is 9 merges/round when the area is a clean test gap
  with no source edits required.
- **Codex unsolicited proactive review.** Codex picked up
  `r7-solver-family-registry-tests` from origin and ran a
  read-only review without an explicit handoff event from Claude.
  Bridge `events.jsonl` now records that pattern as a Codex
  capability rather than a one-off.
- **Watcher batching.** `until ... do sleep ... done` background
  loops queued up to 4 PR auto-merges in parallel, all completing
  on their own as CI cleared.

## What did not work

- **Branch confusion mid-implementation.** PR #114 (deterministic
  compiler) commit landed on the `r6-dream-request-pack-tests`
  branch by accident due to checkout timing. Cherry-pick + force-
  push fixed it but it cost ~4 minutes. Fix forward: re-verify
  `git branch --show-current` immediately before any `git add` in
  a loop with frequent branch switches.
- **`--match-head-commit` SHA truncation.** First merge attempt for
  PR #105 in R5 used the 7-character abbreviated SHA where the full
  40-character SHA was required. Re-fetch the SHA from
  `gh pr view --json headRefOid` before every merge command.

## What still needs an operator

- Merging PR #108 (GPT consensus gate bootstrap, awaiting operator
  curated merge by protocol design).
- Pasting any non-test PR's GPT release-review request (the gate
  takes effect only when a non-test merge happens, which has not
  occurred since R5 PR #106).

## Cumulative session context

R4 + R5 + R6 + R7 round-1 totals (this single session, no operator
paste-relay between merges):

| Round | PRs merged | New assertions | Source-fix |
| --- | --- | --- | --- |
| R4 | 4 | 72 | 0 |
| R5 | 5 | 40 | 1 (`route_engine._quality_counts` lockstep) + 1 contract extension (`review_bundle.risk` field) |
| R6 | 3 | 50 | 0 |
| R7 | 9 | 151 | 0 |
| **Total** | **21 PRs** | **313 assertions** | **1 + 1** |

Plus PR #108 still awaiting operator manual merge.

## Reproducible artifact set

- Round 3 scout: `iterations/codex_scout_tasks/waggle_test_gap_candidates_round3_2026_05_09.md`
- All R7 commits land in main; `solver_synthesis` test directory:
  `tests/solver_synthesis/test_*.py` (10 files post-R7).
- Bridge event log: `.agent-bridge/shared/events.jsonl` (gitignored).
- Hardening gates after R6 merges: PASS (30/30 gates; logged at
  `docs/runs/hardening_gates/<utc>.json`).

## Next round queued

Scout round 4 handoff sent to Codex at 2026-05-09T03:01Z covering
unscanned areas (`learning`, `planning`, `capabilities`, `policies`,
`identity`, `world`, `world_model`, `conversation`, `orchestration`,
`ingestion`, `hex_topology`, `capsules`, `memory_tiers`). When Codex
publishes the round-4 candidate set, R8 begins.
