# Dream Mode Agenda

**Status:** operator-authorized 2026-05-20
**Companion docs:** `IDLE_PROTOCOL_V1.md`, `IDLE_AUTONOMY_CHARTER.md`,
`IDLE_LOOP_RUNBOOK.md`, `IDLE_CONSENSUS_ARTIFACT_V1.md`.
**Companion tools:** `tools/idle_loop_once.py`, `tools/agent_next_task.py`,
`tools/idle_protocol_activate.py`.
**Companion tracking file (still authoritative for measured comparisons):**
`docs/benchmarks/COMPETITIVE_EVIDENCE_MATRIX_2026.md` (snapshot
2026-05-05; needs periodic refresh — see §Cadence).

## Purpose

When the idle-loop substrate fires a tick and the bridge has no
incoming work, agents (Claude and Codex — not the operator) must have
*strategic* options to pick from, not only tactical bug-fix smoke
runs. This file is the canonical strategic backlog the agents poll
during dream-mode deliberation cycles.

Operator framing 2026-05-20 (verbatim, paraphrased to English):

* *"dream and idle are for you large dev models, so you continuously
  develop the WD project to be better than competitors"*
* *"this includes competitor tracking and security/data-protection
  caretaking, but tied to the WD project and its sub-areas"*
* *"Tesla is not the only competitor; there are many others"*
* *"their development was already tracked but apparently got
  forgotten"* — referring to
  `docs/benchmarks/COMPETITIVE_EVIDENCE_MATRIX_2026.md`

This agenda exists so that the LLM↔LLM dream-mode cycle does not drift
back into bug fixes; every idle round should advance one entry in
this file.

## Out of scope

* General AI news with no WD sub-area mapping.
* Operator interaction (dream-mode is agent-to-agent only; operator
  monitors, merges, overrides — but does not drive each cycle).
* End-to-end neural-net training of charter constraints. The 5-
  ingredient roadmap below is explicit: WD's V12-equivalent is
  *primitive substrate → fleet-learning substrate*, **not** modular →
  end-to-end. See §"Five-ingredient roadmap" below and
  `IDLE_AUTONOMY_CHARTER.md` for why end-to-end would destroy
  audit-truth.
* Topics outside the WD sub-area map below.

## Five-ingredient roadmap

Operator-authorized strategic backbone (2026-05-20). The dream-mode
cycle drives WD toward fleet-learning parity by advancing these five
ingredients, **without** collapsing into an end-to-end model that
would swallow charter constraints:

1. **MAGMA receipt v1** — signed evaluation/runtime receipts.
   *Status:* SHIPPED. Code: `waggledance/core/magma/receipt.py`,
   schema: `schemas/v3_13_0/magma_receipt.v1.json`, tests:
   `tests/unit/test_magma_receipt_emitter.py`.
2. **Multi-instance replay flywheel** — cross-instance signed MAGMA
   share with sanitization contract. *Status:* not yet built; design
   surface only (see §C / §D seeds below).
3. **Counterfactual eval pipeline** — replay a stored consensus
   against a candidate diff. *Status:* partial; see
   `tools/run_pdam_counterfactual_demo.py`,
   `tests/night_learning_v2/test_dream_counterfactual.py`,
   `schemas/v3_13_0/evaluation_result.v0.json`.
4. **Hex-cell competitive promotion** — promotion lifecycle within a
   hex cell driven by measured wins, not single-solver hand-promotion.
   *Status:* partial; provenance surface exists
   (`waggledance/core/v3_13_0/solver_provenance.py`,
   `waggledance/core/solver_synthesis/solver_candidate_store.py`);
   competitive step still missing.
5. **Synthetic adversarial corpus** — minimal adversarial example set
   both agents must catch before a candidate solver is promoted.
   *Status:* seed fixtures only
   (`tests/fixtures/magma_adversarial_corpus/v0.json`,
   `tools/validate_synthetic_adversarial_corpus.py`,
   `tools/run_magma_adversarial_eval.py`).

The roadmap exists to keep dream-mode iterations narrow: each idle
round should move exactly one ingredient one step, not branch into
unrelated agent-framework features.

## WD sub-area map

The dream-mode cycle iterates over THESE sub-areas (not the global AI
landscape):

| Sub-area | Primary code path | Strategic-iteration questions |
|---|---|---|
| **Autonomy substrate** | `waggledance/core/autonomy/`, `autonomy_growth/` | Where is the next inner-loop bottleneck? Which provider-jobs-delta=0 invariant is at risk? |
| **MAGMA** | `waggledance/core/magma/`, `schemas/v3_13_0/magma_*` | Receipt v1 adoption progress; verify-manifest scale; counterfactual-eval pipeline (5-ingredient #3). |
| **Hex topology** | `waggledance/core/hex_topology/`, `hex_cell_topology.py`, `configs/hex_cells.yaml` | Competitive promotion within cell (5-ingredient #4); routing latency vs. cell count. |
| **Solver synthesis** | `waggledance/core/solver_synthesis/`, `builder_lane/`, `provider_plane/` | Gap→spec→solver path measurable improvements; specialist promotion thresholds; provider-jobs-delta budget. |
| **Idle protocol substrate** | `waggledance/core/idle_protocol*`, `idle_consensus_*`, `tools/idle_*` | Reach more rounds without false convergence; reduce stale-claim noise; lift "deferred" items from `IDLE_PROTOCOL_V1.md`. |
| **Bridge protocol** | `.agent-bridge/`, `tools/bridge_*`, `tools/work_queue*` | Two-agent activation loop (deferred); read/write-claim race surface; next-action accuracy. |
| **Local intelligence + distillation** | `waggledance/core/local_intelligence/`, `LOCAL_MODEL_DISTILLATION.md` | Local-first claim vs. cloud-fallback budget; sweep against newer Ollama models. |
| **World model + reality view** | `waggledance/core/world_model/`, `waggledance/ui/hologram/` | Truthfulness audit cadence; calibration-drift detector progress. |
| **Synthetic adversarial corpus** | not yet built (5-ingredient #5) | Pure greenfield slice; design first; smallest seed corpus. |
| **Multi-instance replay flywheel** | not yet built (5-ingredient #2) | Sanitization contract; cross-instance signed MAGMA share. |

## Strategic seed categories

The agents pick one seed per idle round. A seed is a `idle_proposal`
payload whose `proposes_substrate_change` is true and whose content
addresses one of the categories below.

### A. Competitor tracking (WD-lens only)

Refresh `docs/benchmarks/COMPETITIVE_EVIDENCE_MATRIX_2026.md` and
adjacent files. Allowed seeds:

* "Audit COMPETITIVE_EVIDENCE_MATRIX_2026 vs. current main — flag
  every claim whose evidence artifact is older than 30 days."
* "Sweep the latest Ollama release against
  `LOCAL_OLLAMA_MODEL_SWEEP_2026` and propose adding/removing a
  baseline model."
* "Compare a newly published rival's local-runtime claim (vLLM,
  llama.cpp, MLC-LLM, OpenLLM, Aphrodite, ...) against the WD
  matrix's axes A–N; mark which axis if any becomes
  NOT CLAIMED for WD."
* "Map a newly published Tesla FSD release note onto the
  five-ingredient roadmap (see §Five-ingredient roadmap above) and
  report whether any ingredient's smallest-safe-step needs to change
  order."
* "Compare a newly published rival agent framework (LangGraph,
  AutoGen, LangChain, LlamaIndex, OpenAI Agents SDK, Crew, …) against
  WD's bridge / idle-protocol substrate; flag missing primitives."

Boundaries:

* Tesla FSD is **one analog** in the roadmap, not a primary
  competitor. WD's primary competitor lens is local-first cognitive
  runtimes and agent frameworks.
* Never propose adopting a competitor's design that would violate the
  charter (e.g., end-to-end weights that swallow charter constraints).
* All competitor-tracking seeds MUST tie back to an axis or sub-area
  in this file. No general AI-news round-ups.

### B. Security / data-protection caretaking

The substrate-defensive invariants in `IDLE_AUTONOMY_CHARTER.md` and
`SUBSTRATE_INVARIANTS.md` are the spec. Allowed seeds:

* "Scan the last 14 days of merged diffs for `PRIVATE_MARKER` /
  `_DO_NOT_LEAK` / `*creds*` / `*token*` / `.env*` patterns
  inadvertently exposed (path or content)."
* "Audit the autonomous-merge denylist (file paths + code patterns)
  against the actual merged diffs from the last 30 days — did any
  denylisted pattern slip through? If yes, where does the gate fail?"
* "Verify the redaction pipeline (`bridge_llm/redactor.py`) still
  catches the same set of synthetic test cases it did at last green
  release; flag any new bypass."
* "Audit the charter escape-hatch invariant: from a paused-loop state,
  can the operator reach 100% loop-stop in ≤ 3 commands? If any
  layer (Disable-ScheduledTask, work-queue release, charter revert)
  has regressed, propose the smallest-safe fix."
* "Sweep the bridge event log for unexpected agent IDs, malformed
  payloads, or events that should have been refused by the schema
  guard but landed in `shared/events.jsonl`."

Boundaries:

* No CVE chasing in unrelated dependencies — only WD substrate
  pieces.
* No reverse-engineering of competitor security stances. Just our
  own substrate.

### C. WD sub-area improvement (smallest-safe-step bias)

For one sub-area at a time (rotate per round), find the smallest
demonstrable improvement that ships as a PR ≤ 400 LoC and passes the
7 charter conditions for autonomous merge. Allowed seeds:

* "Audit recent PRs touching sub-area X; identify the slowest-paced
  invariant; propose the smallest test or refactor that locks it in."
* "Identify a 'deferred' item in any architecture doc (PHASE_9,
  EIG2 ADRs, IDLE_PROTOCOL_V1.md §Deferred, MAGMA_VECTOR_STAGE2
  TODOs) that is the closest to being safely lifted; propose the
  promotion PR."
* "Profile the inner loop's slowest read path on a fresh
  reproducible benchmark; propose the smallest measurable
  improvement."
* "Identify one missing test in `tests/contracts/` that would have
  caught a recent regression; add it."

### D. 5-ingredient roadmap drive

This is the strategic backbone (see §Five-ingredient roadmap above for
the enumeration and current per-ingredient status). Allowed seeds:

* Ingredient #2 (multi-instance flywheel): "Sketch a sanitization
  contract for cross-instance MAGMA share that survives charter
  audit; no code yet, just the contract surface."
* Ingredient #3 (counterfactual eval pipeline): "Identify the
  smallest extension to `tools/idle_consensus_artifact.py` that
  would let a stored consensus be replayed against a candidate diff
  later."
* Ingredient #4 (hex-cell competitive promotion): "Inventory the
  current solver promotion lifecycle (`solver_provenance.py`,
  `solver_candidate_store.py`); identify the missing
  competitive-promotion step; propose the smallest schema or test."
* Ingredient #5 (synthetic adversarial corpus): "Design the seed
  format for adversarial examples; smallest first 5 examples both
  agents should catch."

## Cadence

* **Tick interval:** the existing Windows Task `WaggleDanceIdleLoopOnce`
  fires every 30 minutes (per `IDLE_LOOP_RUNBOOK.md`).
* **Competitor matrix refresh:** target ≤ 14-day staleness for any
  PROVEN/MEASURED row. The first dream-cycle Tuesday-ish after a
  competitor releases something WD-relevant should advance one of
  the §A seeds.
* **Security sweep:** target ≤ 7-day cadence for §B seeds. One
  security-audit seed per week minimum.
* **Sub-area rotation:** §C seeds rotate through the sub-area map
  table at most once per UTC day (no sub-area visited twice in 24h).
* **5-ingredient drive:** at least one §D seed per UTC day until
  ingredients 2–5 reach SHIPPED parity with ingredient 1.

## Charter alignment

This file is **operator-authorized strategic backlog**, not an
executable script. It does not weaken any existing gate:

* All §A–D seeds are eventually realized through the existing
  idle-protocol.v1 + idle-autonomy-charter substrate. The 7 parallel
  conditions for autonomous merge remain enforced by
  `tools/idle_consensus_auto_merge.py`.
* Adding this file does not require self-modification of any
  protected charter doc; it lives at `docs/architecture/` (allowlist
  match), is not on the file denylist, and contains no operator-gate
  constants.
* The cadence prescriptions above are *targets*, not gates. The
  charter's emergency-stop layers (disable scheduled task; charter
  revocation; bridge instruction) remain authoritative.

## How agents read this file

When `tools/agent_next_task.py` returns `claim_substrate_smoke`
because the bridge is otherwise unblocked, a live agent (Claude or
Codex) should:

1. Read this file's §A–D seed lists.
2. Pick the first seed whose category is "in-cadence" per §Cadence
   (e.g., a security seed if § B has not been advanced this week).
3. Claim a write/read-only task for that seed (`task_id` like
   `dream-mode-<category>-<short-slug>-<utc-date>`).
4. If the seed produces a candidate diff or design, route it through
   the existing chain: `idle_consensus_artifact` →
   `idle_consensus_draft_pr` → `pr_status_snapshot` →
   `idle_consensus_auto_merge --apply`.
5. If no seed is in-cadence, fall back to the substrate-smoke pool
   currently in `tools/agent_next_task.SUBSTRATE_SMOKE_CANDIDATES`.

A follow-up PR (Slice 5) wires the cadence-aware seed selection into
`agent_next_task.py` directly; until then the cadence is a manual
discipline the agents follow when they pick a round-1 proposal.

## Versioning

* v0 (this doc): initial backlog enumeration. Operator-authorized
  scope: competitor tracking + security + WD sub-area improvement +
  5-ingredient drive.
* Future versions should:
  - Add or remove seed templates as the WD sub-area map evolves.
  - Tighten cadence numbers based on measured stale-evidence in
    `COMPETITIVE_EVIDENCE_MATRIX_2026.md` between v0 and the
    next-version review.
  - Reference any new substrate file that absorbs part of this
    backlog (e.g., if Slice 5 lands the cadence logic in code).
