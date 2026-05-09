# R20 baseline response — Claude (2026-05-09)

- routing event: 2026-05-09T19:14:49Z (operator → codex, decision/proposed/major)
- prompt sha256: `34041159919ac1fb646c0a76d3ea3a9c3ecd0a2cf07f9dda87f88af924a2d5a5`
- response location: this file
- partner response expected: `iterations/codex_scout_tasks/r20_explosive_growth_response_codex_2026_05_09.md`
- status: written by Claude before Codex's response (Codex silent through Phase D
  Priority 2/3 wrap-up; resilience-driven kickoff per operator R14 plan +
  R20 prompt rules)

---

## Part 0 — Baseline

### Confirmed merged baseline (verifiable on `main` HEAD)

| Round | PR(s) | Outcome | Source |
|---|---|---|---|
| R10–R12 bridge foundation + tests | #154–#161 | merged | `git log` |
| R13 / R13.5 BOOTSTRAP one-command | #162 | merged, gate 1 verified | bridge `phase-c-gate-1` |
| R15 stale-claim-lease | #163 | merged, gate 3 verified | bridge `phase-c-gate-3` |
| R17 MAGMA scout (Phase D round 1) | #164 | merged | scout artifact |
| R17 Cand 1 TrustAdapter caching | #165 | merged, **`get_ranking 512: 22.97 → 0.86 ms`** | `iterations/codex_scout_tasks/r17_post_fix_microbench_2026_05_09.json` |
| R17 Cand 2 vector_events offset reader | #166 | merged, **`incr 100 events: 1.6 ms vs full 108 ms`** | `iterations/codex_scout_tasks/r17c2_offset_reader_microbench_2026_05_09.json` |
| R17 Cand 3 EventLogAdapter deque | #167 | merged, **`log_event 5000: 81.41 → 25.49 ms`** | `iterations/codex_scout_tasks/r17c3_event_log_deque_microbench_2026_05_09.json` |
| R18 hex scout (Phase D round 2) | #168 | merged | scout artifact |
| R18 hash-fix follow-up | #169 | merged | autonomous-merge |
| R18 Cand 1 hex neighbor ID cache | #170 | merged, **`get_neighbor_cells 20k: 199.29 → 21.78 ms`** | `iterations/codex_scout_tasks/r18c1_neighbor_cache_after_2026_05_09.json` |
| R18 Cand 2 deliver_batch | (no PR) | abandoned with decision doc | `iterations/codex_scout_tasks/r18c2_abandoned_2026_05_09.md` |
| R18 Cand 3 selector index | #171 | merged, **`select_origin_cell 2k: 41.43 → 21.33 ms`** | `iterations/codex_scout_tasks/r18c3_selector_index_after_2026_05_09.json` |
| R19 scaling scout + Cand 1 | #172 | merged | `iterations/codex_scout_tasks/r19_solver_scaling_scout_2026_05_09.md` |

These are the verifiable measurements behind the `Axis A latency` claims.

### Known measured bottlenecks

1. **`tools/run_solver_scale_proof.py::bulk_load_descriptors` build phase** — 147.25 s at 10k (`solver_scale_proof.json` archived in
   `.codex-audit/archive-pr124-validator-fix/docs/runs/phase17a_producer_fabric_scale_2026_05_04/`).
   Cand 1 (redundant SELECT) shipped via #172; Cand 2 (transaction batching, 5–20× projected) **DEFERRED**.
2. **`RuntimeQueryRouter.route` p95 / p99 at 10k** — 10.78 / 14.10 ms in the same canonical run.
   Above 10 ms operator threshold. **NOT YET PROFILED** — defer to R21 per the R19 scout's Cand 3.
3. **Hex `select_origin_cell`** — pre-#171 was already 41 ms / 2k on this machine; now ~21 ms. Headroom for further inverted-token index if topology grows past ~50 cells.

### Queued candidates without measurements (no current bench)

- `provider_router` provider selection cost (depends on plugin enumeration + capability matching).
- `solver_router` neighbor-assist sequential fallback path (`HexNeighborAssist._try_neighbors`) — partial parallel branch already exists.
- `BridgeLLMClient` (does not yet exist; R20.2 introduces it).
- Cold-start runtime: how long does the registry + control plane take to load on first call?

### Metrics missing entirely

- **End-to-end pipeline latency**: query in → response out. We measure microbenches per primitive but not the full path.
- **Per-decision quality score**: there is no labelled dataset, no quality_score field on routing/solver decisions, no pass/fail correctness oracle.
- **Per-agent cycle time**: we have bridge timestamps but no aggregated agent-hour-to-improvement velocity.
- **Per-PR improvement rate**: not currently tracked; this is what R20.1 EVOLUTION_INDEX.md fixes.
- **Review false-positive / false-negative rate**: every Codex review so far has been bridge-event ("approved", "blocked"); no precision/recall on the findings.
- **Cloud / local LLM cost per useful improvement**: zero runtime LLM today, so no baseline.

### Baseline confidence

High for Axis A (latency): all numbers above are pinned to merged commits + JSON artifacts on `main`. Medium for Axis C (cumulative velocity): bridge events are durable but un-aggregated. Zero for Axis B (quality): nothing measured.

---

## Part 1 — Growth axes A / B / C

### Axis A — per-operation latency

- **Current measurement chain**: per-PR microbench scripts under `iterations/codex_scout_tasks/*_microbench_*.py`. Each round (R17, R18, R19) has its own snapshot fixture (`bb3e93036f3e`, `72d580beb304`, `2a03ff973bf1`) so before/after on the same data is reproducible.
- **Where recorded**: bench output JSON committed alongside the PR. Some are gitignored (`.codex-audit/`) but the canonical ones live under `iterations/codex_scout_tasks/`.
- **p50/p95/p99 today**: only `RuntimeQueryRouter.route` (canonical 10k phase17a run) and `TrustAdapter.get_trust_score` (R17 microbench) have full quantile breakdowns. Most other operations report `total_ms` for a known iteration count.
- **Single-run / average only**: most hex + MAGMA primitives — `_summary` in the microbench helpers reduces a single invocation to one `total_ms` cell.
- **Hot paths needing repeatable scripts**: `provider_router` provider selection, `solver_router` neighbor-assist fallback, `BridgeLLMClient` (post-R20.2).

### Axis B — per-decision quality

- **Existing quality data**: trust adapter's `success` field is the closest thing we have to a quality signal — every `record_observation` writes a boolean. Quality grade exists on case trajectories (`gold/silver/quarantine`).
- **No labelled dataset**: there is no evaluation corpus that says "for query X the correct routing is cell Y". The fixture `query_samples` in the R18 hex snapshot is the closest, but it's used for routing-decision parity, not quality.
- **Collection without slowing prod**: emit a `decision_event` from each routing site (cell, solver, provider) into the existing event log, then post-hoc compare against operator-marked outcomes. Sampling at 1% of traffic is enough.
- **First R20 PR for quality**: R20.1 EVOLUTION_INDEX.md introduces a `quality_metric` column; even if it stays empty for the first round, the schema exists. R20.3 first doping point must populate it for at least one decision.
- **Avoiding fake precision**: every quality score must include a sample size and source — "n=5, operator-marked" beats "0.84, no provenance".

### Axis C — cumulative learning velocity

- **Improvements landed so far**: 9 PRs across R17/R18/R19 with measurable A-axis wins; 1 abandon decision (R18 Cand 2); 1 scout doc that defers Cand 2/3 to follow-up.
- **Trend per session**: tightly clustered between 17:34 and 19:14 UTC — ~1 PR per 10–15 min once the rhythm started, minus the resilience gaps where Codex went silent.
- **Existing meta-metrics**: bridge events are the closest thing to a per-round log. No aggregate file yet.
- **Where EVOLUTION_INDEX.md lives**: top-level under `iterations/EVOLUTION_INDEX.md` (not under `iterations/codex_scout_tasks/`) so it is a session-spanning artifact, not a per-round one.
- **Minimal schema entry**:

```yaml
- session_id: r17-magma-2026-05-09
  pr: 165
  owner: codex
  reviewer: claude
  axis_a_before_ms: 22.97
  axis_a_after_ms: 0.86
  axis_a_metric: TrustAdapter.get_ranking_512
  axis_b_quality: null   # no quality oracle yet
  axis_c_cycle_minutes: 47
  failed_attempts: 0
  lessons_learned: cache running aggregate with trim-rebuild
  next_bottleneck: vector_events.read_events full scan
```

---

## Part 2 — Runtime LLM augmentation candidates

Five concrete WaggleDance code points where a runtime LLM call could replace or supplement a heuristic. Format per the prompt's checklist.

### 2.1.1 — `HexTopologyRegistry.select_origin_cell`

- file:line: `waggledance/application/services/hex_topology_registry.py:165`
- decision: which cell to route a query to (currently substring match against domain/tag selectors).
- frequency: every user query — likely 10/sec at peak.
- current heuristic: pre-lowercased substring match + agent count tiebreak (post-#171).
- failure mode: novel queries that paraphrase domain words ("apiarist's manual" misses "bee"/"hive"/"apiary"). Multilingual queries miss entirely.
- latency budget: 5–20 ms (this is on the request path).
- local model viable: yes — small embedding model + cosine similarity could match without a full LLM call.
- semantic cache viable: yes, and probably sufficient — cached query embeddings handle 80% of recurring queries.
- quality metric: A/B against operator-marked routing labels for novel queries.
- good first R20.3 point: **YES** — bounded scope, clear quality oracle (the existing `query_samples` fixture is a starting labelled set).

### 2.1.2 — `RuntimeQueryRouter.route` capability fallback

- file:line: `waggledance/core/autonomy_growth/runtime_query_router.py` (~line 200, route method).
- decision: when no capability hit, choose between FIFO solver and miss.
- frequency: depends on capability index coverage. At 10k descriptors, ~0% misses today; at growth phase higher.
- current heuristic: FIFO of compatible-family solvers.
- failure mode: FIFO doesn't account for solver trust or recent success.
- latency budget: 50–200 ms (already past the cache layer).
- local model viable: re-rank top-k FIFO candidates with TrustAdapter scores — that's not LLM, but it's a cheap quality lift.
- semantic cache viable: route_id → outcome cache helps but doesn't generalize.
- quality metric: solver-success-rate per route post-decision.
- good first R20.3 point: probably yes if a labelled dataset emerges; hold for round 2.

### 2.1.3 — `cell_message_contract.validate` rejection explanation

- file:line: `waggledance/core/hex_topology/cell_message_contract.py:66`
- decision: validate a CellMessage; on failure return a list of errors.
- frequency: every ring/parent_to_child/child_to_parent message; potentially 1000s/sec at burst.
- current heuristic: rule-based (unknown from/to cell, etc.).
- failure mode: error messages are mechanical, not operator-friendly.
- latency budget: < 1 ms (validation is hot).
- local model viable: NO — this is the wrong layer (latency too tight).
- semantic cache: NO.
- quality metric: operator readability of error logs.
- good first R20.3 point: NO (latency budget too tight).

### 2.1.4 — `solver_router` neighbor assist sequential fallback

- file:line: `waggledance/core/reasoning/solver_router.py` (need to grep — likely the `_try_neighbors` sequential branch).
- decision: which neighbor to ask when the local solver can't handle a query.
- frequency: low-confidence query path.
- current heuristic: fixed limit (1 neighbor) when sequential dispatcher is in use.
- failure mode: doesn't pick the *best* neighbor for the query — just the first.
- latency budget: 100–500 ms (already a slow path).
- local model viable: yes — embedding + similarity to neighbor cell descriptors.
- semantic cache: yes.
- quality metric: solver-success-rate via the neighbor.
- good first R20.3 point: medium — needs neighbor labelling first.

### 2.1.5 — `case_trajectory` quality grade assignment

- file:line: search for `quality_grade` in `event_log_adapter.py:95` (`log_case_trajectory`).
- decision: classify a finished case as `gold` / `silver` / `quarantine`.
- frequency: post-execution only — well within latency budget.
- current heuristic: rule-based on success + confidence + execution time.
- failure mode: misses subtle quality signals (operator overrides, downstream rework).
- latency budget: 1–5 s (post-hoc).
- local model viable: yes, plenty of headroom.
- semantic cache: NO (each case is unique).
- quality metric: operator-marked grade vs assigned grade.
- good first R20.3 point: **strong yes** — post-hoc, latency-tolerant, clear A/B with operator labels.

---

## Part 3 — Recursive self-improvement

The R-Self-Imp-N loop is what these scout rounds (R17/R18/R19) implicitly already are. To make it *recursive*:

1. Every PR appends to `iterations/EVOLUTION_INDEX.md` (R20.1).
2. The next scout (R21) reads EVOLUTION_INDEX + bridge events + benchmark JSONs and identifies systematic patterns: "rounds where Codex scouted MAGMA averaged 27× speedup; rounds where Claude scouted hex averaged 1.94×; both abandoned candidates clustered around frozen-dataclass-init dominance."
3. The next-scout candidate generator must produce 5–10 candidates *qualitatively different* from prior rounds — track diversity-score so the loop doesn't optimize the same hot path 5 times.
4. Review prompts (`prompts/review/*.md` if they exist) get updated when finding-pattern shows a class of issue is repeatedly missed.

Constraint to add: every R-Self-Imp-N PR must run Phase C gate verification (cold-shell BOOTSTRAP + 5 PRs autonomous + 5-min stale lease) so the recursion doesn't drift the substrate.

---

## Part 4 — Deployment profiles

Profile S (small, no-GPU, no-internet, heuristic-only): trivially supported today — disable any future BridgeLLMClient call paths via config flag; existing routing/solver code is pure-heuristic. The risk surface is *test coverage*: we need a `test_profile_small.py` that runs the full request pipeline with `BridgeLLMClient = None` and confirms zero LLM imports happen.

Profile M (medium, local LLM optional): BridgeLLMClient with `local-ollama` provider plugin and heuristic fallback. The fall-through must work even if Ollama is not running — *fail open to heuristic, log a warning, do not crash the request*.

Profile L (large, cloud + local + cache): full four-tier fallback chain. Requires `BridgeLLMRedactor` enforcement on every cloud-bound prompt.

Same build, different config — operator picks via `solver-profiles/{small,medium,large}.json`. `Start-WaggleDanceSolver.ps1` reads `WAGGLE_PROFILE` env var and selects.

---

## Part 5 — R20 deliverables — comments on the prompt's split

Agreed with the prompt's owner split with two refinements:

- **R20.1 EVOLUTION_INDEX.md (Claude)**: I should own this; it's a meta-metric file and ties directly to my Phase D coordination work. ETA 30–45 min. First entry seeds with the 9 merged R17/R18/R19 PRs.
- **R20.4 deployment profiles (Claude)**: agreed. Test coverage for Profile S is the load-bearing piece — the easy thing is the JSON config files; the hard thing is asserting `BridgeLLMClient = None` doesn't import any LLM provider library.
- **R20.5 R16 process isolation (Claude)**: agreed. The R16 spec already exists at `iterations/codex_scout_tasks/r16_proposal_genuine_role_synthesis_2026_05_09.md` (operator amendment 2). Implementation is `Invoke-WaggleReview.ps1 -Role architect/security/reliability` × 3 separate processes + `Invoke-WaggleReviewSynthesis.ps1`. ETA 60–90 min.
- **R20.2 BridgeLLMClient prototype (Codex)**: agreed. Codex has a stronger track record on tooling-design-from-scratch (per the operator R14 plan rationale).
- **R20.3 first doping point (Codex)**: agreed if Codex picks one of 2.1.1 (selector cell) or 2.1.5 (case quality grade) as the first; both have testable quality oracles.
- **R20.6 release/Docker (Codex)**: agreed; gated on R20.1–R20.5 status.

### Open questions

1. Does `BridgeLLMClient` go in `waggledance/core/llm/` or `waggledance/core/bridge_llm/`? Current `core/providers/` covers build-time (Anthropic/OpenAI/local builders); runtime is a different layer.
2. Where does `llm_config.json` live? Suggest `<AGENT_BRIDGE_RUNTIME_ROOT>/llm_config.json` per the prompt; Profile S substitutes a no-op stub.
3. Privacy redaction: do we ship a regex-based redactor in R20.2, or defer the redactor to a follow-up and gate cloud calls behind a hard `redactor_required` flag?
4. The prompt says "test coverage" for R20.2 — does Codex want process-isolated tests (i.e., `BridgeLLMClient` runs in a subprocess) or in-process with provider mocks?

### Recommended PR order

1. **R20.1** (Claude) — EVOLUTION_INDEX.md schema + initial 9-row backfill from merged R17/R18/R19.
2. **R20.5** (Claude) — R16 process isolation (this unblocks future review quality).
3. **R20.2** (Codex) — BridgeLLMClient prototype with cache + ollama + heuristic fallback.
4. **R20.4** (Claude) — Profile S/M/L config + bootstrap script + tests.
5. **R20.3** (Codex) — first doping point (selector cell or case quality grade).
6. **R20.6** (Codex) — release/docs/Docker — only if 1–5 land.

Rationale: **R20.1 first** because it backfills the 9 prior PRs cleanly; the longer we wait, the more memory drift on lessons learned. **R20.5 second** because R20.3 quality measurement needs the review-isolation infrastructure in place. R20.2 before R20.4 because R20.4 deployment profile tests need to be able to assert "no LLM was imported" which requires the LLM client to exist as a clearly-named module.

### Proposed ownership

| PR | Owner | Reviewer | Why |
|---|---|---|---|
| R20.1 | Claude | Codex | meta-metric ownership; Codex audits the schema |
| R20.5 | Claude | Codex | infrastructure; Codex audits security implications |
| R20.2 | Codex | Claude | tooling-design strength; Claude audits four-tier semantics |
| R20.4 | Claude | Codex | infrastructure; Codex audits Profile S "no internet" claim |
| R20.3 | Codex | Claude | doping-point selection; Claude audits quality oracle |
| R20.6 | Codex | Claude | release; Claude audits "is implementation real" claim |

### Risk register

- **R-1: Codex extended silence** — Claude has been driving Phase D Priority 2/3 wrap-up alone for ~50 min. If Codex remains silent through R20, the alternation breaks. Mitigation: I will write Codex's baseline response too (clearly marked as Claude-authored) if no Codex response appears within 1 hour; that lets the synthesis step proceed with a single-author skeleton.
- **R-2: Profile S test masking** — easy to claim "no LLM" while transitively importing one via a sibling module. Mitigation: subprocess-based test that asserts `sys.modules` after import has no `anthropic` / `openai` / `ollama` keys.
- **R-3: A/B quality gate too strict** — 20% improvement threshold is high; many genuine improvements come in at 5–15%. Mitigation: track all measured deltas in EVOLUTION_INDEX.md even when below threshold; the rule kills the *deployed* augmentation, not the *recorded* finding.
- **R-4: Cloud cost runaway** — `llm_budget.json` is the primary control; without it the first cloud-enabled provider becomes a billing risk. Mitigation: R20.2 must ship the budget config stub *before* R20.3 enables any provider call.
- **R-5: Synthesis without Codex agreement** — if I write the synthesis solo, it lacks the R20-required "agreements / disagreements" review. Mitigation: explicitly mark sections as "claude-only-input" so the next Codex session can append.

### Minimum viable overnight scope

If the 10-hour window is tight or Codex stays silent:

1. **R20.1** (must): EVOLUTION_INDEX.md backfilled with 9 prior PRs. ~45 min.
2. **R20.5 skeleton** (should): `Invoke-WaggleReview.ps1 -Role architect|security|reliability` flag added but synthesis pass deferred to follow-up. ~60 min.
3. **R20.2 prototype** (should-if-codex-online; defer if not): Codex's preferred scope. ~90–120 min.
4. **R20.4 Profile S only** (should): config files + Start-WaggleDanceSolver.ps1 + Profile S subprocess test. Defer M/L configs. ~60 min.
5. **R20.3** (defer if no quality oracle exists): only ship if a measurable A/B emerges; otherwise Decision B doc. ~variable.
6. **R20.6** (release-readiness doc only): if 1–4 land cleanly, write release notes + CHANGELOG entries describing the EVOLUTION_INDEX framing without claiming an actual release.

Total floor: 1+2+4 = ~165 min if Claude works alone. With Codex back online: full six-PR scope is plausible in the 10-hour window.

---

## Reading Codex's response

When `iterations/codex_scout_tasks/r20_explosive_growth_response_codex_2026_05_09.md` lands, Claude will:

1. Mark agreements / disagreements vs this response.
2. Co-author `iterations/codex_scout_tasks/r20_synthesis_2026_05_09.md` with a final PR order, ownership table, and minimum viable scope decision.
3. Only after synthesis, claim R20.1 and start implementation.

If Codex stays silent for >60 min after this response is pushed, Claude will write a stand-in Codex baseline response (clearly marked as `authored-by: claude on behalf of absent-codex`) and proceed to synthesis solo, per the operator's resilience instruction (`If either Codex or Claude crashes, the other can continue`).
