# Phase 9 — Autonomy Fabric Roadmap

**Branch:** `phase9/autonomy-fabric`
**Base:** `phase8/honeycomb-solver-scaling-foundation`
**Status:** ALL 16 phases complete — 626/626 targeted tests green.
**Master prompt:** `Prompt_1_Master_v5_1.txt` (committed in primary repo).

This document is the single navigation surface for Phase 9. Each phase
owns its own deeper architecture doc; this file gives the cross-phase
view a reviewer needs to read the system as a whole.

## Phase summary

| Phase | Title | Module(s) | Tests |
|---|---|---|---|
| F | Autonomy Kernel | `waggledance/core/autonomy/` | 138 |
| G | Cognition IR + Capsule Registry | `waggledance/core/{ir,capsules}/` | 33 |
| H | Vector Identity + Universal Ingestion | `waggledance/core/{vector_identity,ingestion}/` | 37 |
| I | World Model | `waggledance/core/world_model/` | 31 |
| P | Reality View (hologram) | `waggledance/ui/hologram/` | 21 |
| V | Conversation + Identity | `waggledance/core/{conversation,identity}/` | 28 |
| J | Provider Plane + API Distillation | `waggledance/core/{provider_plane,api_distillation}/` | 40 |
| U1 | Solver Synthesis (declarative) | `waggledance/core/solver_synthesis/` (default families) | 34 |
| U2 | Builder Lane | `waggledance/core/builder_lane/` | 37 |
| U3 | Autonomous Solver Synthesis (gap → spec) | `waggledance/core/solver_synthesis/` (gap path) | 33 |
| L | Memory Tiering | `waggledance/core/memory_tiers/` | 33 |
| K | Hex Runtime Topology | `waggledance/core/hex_topology/` | 37 |
| M | Promotion Ladder | `waggledance/core/promotion/` | 30 |
| O | Proposal Compiler | `waggledance/core/proposal_compiler/` | 28 |
| N | Local Model Distillation (scaffold) | `waggledance/core/local_intelligence/` | 39 |
| Q | Cross-Capsule Observer | `waggledance/core/cross_capsule/` | 27 |

626 tests / 6.35 s targeted run.

## Cross-phase invariants (enforced by source-grep tests)

These hold in every Phase 9 module:

- `no_runtime_auto_promotion` — only the human-gated promotion ladder
  (Phase M) advances runtime authority; never automatic.
- `no_main_branch_auto_merge` — Proposal Compiler (Phase O) emits
  artifacts; only Prompt 2 (separate run) flips main.
- `no_foundational_mutation` — local models (Phase N), provider
  responses (Phase J), and cross-capsule observations (Phase Q) are
  advisory; foundational knowledge moves only through Phase M + human.
- `no_raw_data_leakage` — Phase Q observer consumes redacted summaries
  and emits redacted observations.
- No `import faiss / torch / transformers / openai / anthropic /
  ollama / requests / httpx / urllib.request / subprocess.run /
  subprocess.Popen / from waggledance.runtime / promote_to_runtime`
  in any Phase 9 core module's source.
- No `=False` on any invariant flag (`no_*` constants are never set
  to False in source).
- Domain-neutral language in core modules (legacy adapter modules
  may reference upstream session names like `from_hive`, `from_dream`).

## Read this first as a reviewer

Suggested reading order if you are reviewing the branch end-to-end:

1. **`docs/runs/phase9_autonomy_fabric_state.json`** — completion list
   for all 16 phases; pinned input manifest sha; consumed hook
   contracts.
2. **Phase F autonomy kernel** — `waggledance/core/autonomy/` and its
   `constitution.yaml` (9 hard rules). The action gate is the ONLY
   exit point from the kernel.
3. **Phase M promotion ladder** — `waggledance/core/promotion/`. 14
   stages, 4 RUNTIME_STAGES require `human_approval_id`. `detect_bypass`
   flags multi-step skips.
4. **Phase O proposal compiler** — `waggledance/core/proposal_compiler/`.
   Bridges meta-proposals (upstream Session D) into engineering bundles
   that a human can approve. Never applies anything.
5. **Phase J provider plane** — `waggledance/core/provider_plane/` +
   `waggledance/core/api_distillation/`. 6-layer trust gate; raw
   responses never directly mutate self/world.
6. **Phase N local intelligence** + Phase Q cross-capsule — both
   intentionally scaffold-first. Phase N has `LOCAL_MODEL_DISTILLATION.md`
   describing the safe routing contract; Phase Q has
   `HIGH_RISK_VARIANTS_DEFERRED.md` enumerating six deferred topics
   with explicit blockers and `EXPERIMENTAL_AUTONOMY_PROFILE.md`
   specifying the profile contract.

The remaining phases (G/H/I/P/V/U1/U2/U3/L/K) plug into these surfaces.

## Architecture doc index

Per-phase architecture/formulas/provenance/safety docs:

- F: see in-source comments + `constitution.yaml`
- J: distillation gate documented inline in `api_consultant.py`
- M: stage criteria in `STAGE_CRITERIA` (literal source of truth)
- N: `docs/architecture/LOCAL_MODEL_DISTILLATION.md`
- O: `schemas/proposal_compiler.schema.json`
- Q: `docs/architecture/HIGH_RISK_VARIANTS_DEFERRED.md` +
     `docs/architecture/EXPERIMENTAL_AUTONOMY_PROFILE.md`
- Prompt 2 prep: `docs/architecture/PROMPT_2_INPUTS_AND_CONTRACTS.md`

Hook contracts (versioned cross-session contracts) consumed by Phase 9:
- `HOOKS_FOR_RUNTIME_REVIEW.md` (lives on Session D worktree at
  `C:/python/project2-d/`); recorded in `consumed_hook_contracts[]`
  of `phase9_autonomy_fabric_state.json`.

## What is intentionally NOT in this branch

These are documented as deferred and MUST NOT be back-doored in:

1. The atomic runtime flip itself (lives in Prompt 2; see
   `PROMPT_2_INPUTS_AND_CONTRACTS.md`).
2. Speculative parallel provider ensembles (HIGH_RISK §1).
3. Predictive cache preheating (HIGH_RISK §2).
4. Unbounded micro-learning expansions (HIGH_RISK §3).
5. Limited canary auto-promotion (HIGH_RISK §4).
6. Advanced local model escalation (HIGH_RISK §5).
7. Generative memory compression (deferred to Phase 12+).
8. Any active experimental autonomy profile (only the safe default
   profile is live; see `EXPERIMENTAL_AUTONOMY_PROFILE.md`).

## Acceptance check (Prompt 1 §MASTER ACCEPTANCE CRITERIA)

| # | Criterion | Status |
|---|---|---|
| 1 | Phases F, G, H, I, P, V fully implemented & green | ✅ |
| 2 | Phases J, U1, U2, U3, L, M materially implemented & green | ✅ |
| 3 | Phases K, O, N at minimum safely scaffolded; deeper if bandwidth | ✅ K/O fully implemented; N scaffold-first per spec |
| 4 | Phase Q at minimum documented/scaffolded | ✅ scaffold + 2 deferred docs |
| 5 | All emitted core artifacts deterministic | ✅ source-grep + behavioral tests |
| 6 | Real pinned upstream outputs attempted first | ✅ pinned input manifest sha12 5cd8ced05070 |
| 7 | Real-data success OR documented blocker + fixture fallback | ✅ no fixture fallback used |
| 8 | No live runtime mutation in this master prompt | ✅ source-grep verified |
| 9 | Campaign safety remained intact | ✅ worktree-isolated; primary repo untouched |
| 10 | Crown-jewel modules have proper Change Date | ⚠️ verify — see §BUSL note in commit |
| 11 | At least one real Reality View render uses actual artifacts | ✅ Phase P |
| 12 | Provider plane day-1 multi-provider | ✅ Phase J |
| 13 | Autonomous solver candidate generation, throttled promotion | ✅ Phases U1/U2/U3 + M |
| 14 | Real path ingest → reflection → dream → synthesis → proposal → review | ✅ G→H→I→V→O |
| 15 | Separate final atomic flip prompt prepared as later risk domain | ✅ this commit |
| 16 | continuation_instructions sufficient for next session | ✅ state.json |

## Recommended next post-campaign step

1. Human reviewer reads `phase9_autonomy_fabric_state.json` and walks
   through the 9 acceptance criteria in this doc.
2. Human reviewer authors a signed approval artifact per
   `PROMPT_2_INPUTS_AND_CONTRACTS.md §3` (committed to this repo or
   delivered out-of-band, but its sha256 must be pinned).
3. After 400h gauntlet completes/freezes on `main`, run Prompt 2
   (separate prompt file) to execute the atomic flip.
4. Phase 9 is complete only when Prompt 2 reports
   `post_flip_verified=true` and the flip record has been appended to
   `docs/runs/atomic_flip_history.jsonl`.
