# R4 — autonomous bridge-loop test (2026-05-08)

**Branch:** waggledance/r4-final-report
**Status:** AUTONOMOUS LOOP VERIFIED
**Operator paste-relay events during R4 main loop:** 0

## What R4 was

R4 was the first round in this repo where Claude and Codex coordinated
end-to-end through the agent bridge — task selection, implementation,
review, fix iteration, and merge — without the operator pasting messages
between agent windows.

Round 1 of the loop produced three merged PRs against `main`:

| PR | Branch | Squash SHA | Surface | Direct-import test assertions |
| --- | --- | --- | --- | --- |
| #101 | `waggledance/r4-test-gap-fill` | `97ab839` | `waggledance/core/autonomy_growth/family_oracles.py` | 27 |
| #102 | `waggledance/r4-causal-engine-tests` | `d103749` | `waggledance/core/reasoning/causal_engine.py` | 22 |
| #103 | `waggledance/r4-route-engine-tests` | `963a32d` | `waggledance/core/reasoning/route_engine.py` (+ source-bug fix) | 25 |

Net change in `main`: **+74 direct-import test assertions** (27 + 22 + 25)
across three previously test-bare files, plus one real source-bug fix in
`route_engine.py` that Codex's review surfaced.

Per the autonomy-merge guardrails (CLAUDE.md rule 9), every merge required
all four conditions to hold simultaneously: PR head SHA matched the local
expected SHA, every required CI check was green, GitHub `mergeStateStatus`
was `CLEAN` / `MERGEABLE`, and no rule was violated. All three PRs were
merged via `gh pr merge --squash --match-head-commit=<sha> --delete-branch`.

## The loop, step by step

The salient property is that every transition between rows below was
triggered by a bridge event, never by an operator paste. Times are UTC.

| Time | Agent | Event | Triggered |
| --- | --- | --- | --- |
| 13:10 | codex | done — scout output (`iterations/codex_scout_tasks/waggle_test_gap_candidates_2026_05_08.md`) | Claude reads and picks Candidate 1 |
| 18:36 | claude | implementation done — PR #101 (27/27 passing) | Codex starts read-only review |
| 18:53 | codex | done — PR #101 review: no findings | Claude opens Candidate 2 in parallel |
| 19:05 | codex | finding (medium) — PR #102 no-graph fallback tests are conditional no-ops | Claude reads handoff, fixes, pushes |
| 19:07 | claude | message — PR #102 fix pushed (monkeypatch fixture) | Codex re-reviews |
| 19:24 | codex | done — Codex's own Python env now usable (`.python` + `.venv` rebuilt); PR #102 22/22 passing locally | Codex moves to PR #103 review |
| 19:26 | claude | done — PR #103 open (Candidate 3, 23/23 passing) | Codex starts read-only review of PR #103 |
| 19:28 | codex | finding (medium) — PR #103 `_quality_counts` not trimmed in lockstep with `_decisions` | Claude reads handoff, fixes source + adds 2 regression tests |
| 19:29 | codex | done — PR #102 re-review: verified fixed | (no action; informational) |
| 19:29 | codex | done — PR #101 targeted pytest now passes locally for Codex (27/27) | (no action; informational) |
| 19:33 | claude | done — PR #103 fix pushed (source + test) | Codex re-reviews |
| 19:41 | codex | done — PR #103 quality-count trim fix verified by Codex; "Autonomy handoff loop Claude->Codex completed without operator relay." | Claude proceeds to merge phase |
| 19:41–19:43 | claude | autonomous merge × 3 | `main` updated; both branches deleted |

The full event sequence is reproducible from
`.agent-bridge/shared/events.jsonl`.

## Findings the loop surfaced

Two of the three Codex review handoffs were real defects, not stylistic
notes:

- **PR #102 — silent-fallback test no-op.** Four `no-graph` tests in
  `tests/reasoning/test_causal_engine.py` were guarded by `if engine._graph
  is None:`, but `_get_graph_class()` succeeds in this repo because
  `core.cognitive_graph` imports successfully. The guards never fired and
  the assertions never ran — the tests passed without exercising the
  fallback path they advertised. The fix is a `no_graph_engine` fixture
  that monkeypatches `_get_graph_class` to return `None`, asserts
  `engine._graph is None` up front, and removes the guards. Without the
  Codex review this would have shipped to `main` as a silently degraded
  test surface.
- **PR #103 — `_quality_counts` window mismatch (real source bug).**
  `RouteEngine.record_decision` trimmed `_decisions` once it overflowed
  `_max_decisions`, but `_quality_counts` kept an all-time tally. After
  enough records, `stats()["total_decisions"]` (bounded window) and
  `get_quality_distribution()` (all-time tally) reported denominators that
  no longer described the same history. The fix walks the dropped prefix
  in `record_decision`, decrements `_quality_counts`, and deletes keys
  that hit zero. Two regression tests pin the lockstep:
  `test_bounded_history_keeps_quality_counts_in_lockstep_with_decisions`
  and `test_stats_total_decisions_matches_quality_distribution_denominator`.
  This was real product code, landed in `waggledance/core/reasoning/`.

The third handoff (PR #101) had no findings.

## What worked

- **Bridge as both queue and ledger.** Codex and Claude pulled work from
  the same `events.jsonl` and `inbox/<agent>/` paths. Claims served the
  scope-isolation function the operator used to play manually.
- **Operator-style read-only/write split.** Codex stayed read-only +
  pytest-runner; Claude owned write scope. The asymmetry made claim
  conflicts trivial to avoid: a single review never raced an
  implementation.
- **`--match-head-commit` as the merge gate.** Each merge command pinned
  the SHA observed when the autonomy guardrails were checked, so a
  late-arriving push could not silently change what was merged.
- **Re-running stale CI rather than mutating state.** PR #102 hit a
  pre-existing 3.11-only flake (`test_phase17a_producer_fabric_proof.py
  ::test_proof_deterministic_across_two_runs`). The loop re-ran the failed
  job, observed it was non-deterministic on 3.11 specifically, and waited
  for the second pass — it did not patch the flaky test under cover of
  the R4 PR.
- **Codex repaired its own environment mid-loop.** When the Codex shell
  reported "python/py/pytest unavailable," it copied a workspace Python
  3.13.7 to `.python/` and rebuilt `.venv/`, then began running targeted
  pytest locally. From that point the review loop tightened: Codex did
  not need to wait for CI to catch a reviewable test failure.

## What did not need an operator

- Task selection (Codex scout → Claude pick).
- Bug-find → fix → re-verify cycle for PR #102 and PR #103.
- Merge decisions for all three PRs.
- Claim coordination between agents.

## What still needs an operator (intentionally)

- Force-pushes, hard-resets, and any action that rewrites shared
  history (CLAUDE.md golden rules).
- `HUMAN_APPROVAL.yaml` collection for atomic-flip cutovers
  (CLAUDE.md rule 10 — collection is one-shot at execution time).
- Phase initiation that does not extend an existing scout / handoff
  chain. New work surfaces still come in through the operator; what
  the bridge automated is the **execution** of an already-framed
  loop.

## Reproducible artifact set

For a future audit:

- Event log: `.agent-bridge/shared/events.jsonl` (UTC-ordered, append-only).
- Round 1 scout: `iterations/codex_scout_tasks/waggle_test_gap_candidates_2026_05_08.md`.
- Round 2 scout (running at the time of writing):
  `iterations/codex_scout_tasks/waggle_test_gap_candidates_round2_2026_05_08.md`.
- Hardening gates run during the loop: `docs/runs/hardening_gates/2026-05-08T19-08-06Z.json` (30/30 PASS, OVERALL PASS).
- Merged commits in `main`: `97ab839`, `d103749`, `963a32d`.

## Round 2 status (in flight)

At the time this report was written, a second scout handoff was open to
Codex (`waggle-core-test-gap-scout-round2-2026-05-08`) covering
`waggledance/core/` areas not yet exercised by R4 round 1
(`actions/`, `api_distillation/`, `capabilities/`, `learning/`, `magma/`,
`meta/`, `orchestration/`, `planning/`, `policies/`, `solver_synthesis/`).
The expectation is that round 2 will follow the same shape: scout →
implement → review → optional fix iteration → autonomous merge.

If round 2 also closes without operator paste-relay, the bridge-loop
mechanism graduates from "verified once" to "repeatedly self-driving on
test-coverage work."
