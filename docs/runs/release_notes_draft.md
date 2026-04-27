# WaggleDance v3.6.0 — Phase 9 Autonomy Fabric

**Release date:** 2026-04-27
**Branch:** `phase9/autonomy-fabric` (37 commits, 105 ahead of pre-Phase-9 main)
**PR:** #51
**Tests:** 657/657 Phase 9 targeted tests passing in ~7 s; CI 5/5 green

---

## TL;DR

WaggleDance v3.6.0 lands the **autonomy fabric scaffold**: 16 phases of architecture (F–Q) that wire together the always-on cognitive kernel, cognition IR, vector identity, world model, conversation layer, provider plane, builder/mentor lanes, solver synthesis, memory tiers, hex runtime topology, promotion ladder, proposal compiler, local model distillation safe scaffold, and cross-capsule observer.

This release is **review-only**. The atomic runtime flip — pointing the live runtime read path at the new fabric — is intentionally deferred to a separate Prompt 2 session, gated on a signed human approval artifact and the completion of the 400h gauntlet campaign.

## What you get

- A real, end-to-end path from ingest → reflection → synthesis → proposal → review
- A 14-stage promotion ladder where 4 runtime stages require explicit human approval
- A multi-provider plane with a 6-layer trust gate
- A safe local-model scaffold that refuses to route critical tasks
- A cross-capsule observer that emits redacted patterns only
- A Reality View that never fabricates values
- Real evidence artifacts: kernel tick, Reality View render, conversation probe, end-to-end pipeline demo (all using real Session B/D upstream data)

## Highlights

### Always-on cognitive kernel

`waggledance/core/autonomy/` ships 10 sub-components: kernel state, governor (refuses to tick on constitution sha mismatch), mission queue, budget engine, policy core (bounded — may tighten but never relax hard rules), action gate (sole exit point, never executes), attention allocator (8 lanes, deterministic), background scheduler, micro-learning lane (bounded priority adjustments), circuit breaker (closed → half-open → open → quarantined).

### Cognition IR + Capsule Registry

A typed IR for cross-session contracts. Adapters bridge upstream Phase 8.5 producers (`from_curiosity.py`, `from_self_model.py`, `from_dream.py`, `from_hive.py`) into the IR. Capsule registry enforces blast-radius — a request authored under one capsule cannot reach into another capsule's runtime state without raising `BlastRadiusViolation`.

### Vector identity + universal ingestion

4-level dedup (exact / semantic / sibling / contradiction-or-extension), append-only provenance graph with chained event log, identity anchors that enter a candidate state before promotion, three CLI tools (`wd_ingest`, `wd_link`, `wd_identity`).

### World model

Snapshot, delta, external evidence collector, causal engine, prediction engine, calibrator, drift detector. Strictly separated from self_model — verified by source-grep that no Phase I module imports from `waggledance.core.magma`.

### Reality View

11-panel structured operator view. Each panel is either `available=true` with real items OR `available=false` with a structured `rationale_if_unavailable` string. Never papers over missing data with zeros or guesses. Real render evidence committed at `docs/runs/phase9_reality_view_render.json` against Session B `self_model_snapshot.json`: 5/11 panels populated, 6/11 honestly unavailable.

### Conversation layer

5 META_QUESTION_KINDS dispatched to deterministic responders. Forbidden-pattern scanning at render time. Real probe evidence at `docs/runs/phase9_conversation_probe.json`: 4 question kinds answered cleanly with `is_clean=true` and 0 pattern violations.

### Provider plane + 6-layer distillation

Multi-provider routing (Claude, GPT, local Ollama). Every provider response passes through 6 trust layers: `raw_quarantine → internal_consistency → cross_check → corroboration → calibration_threshold → human_gated`. No raw response can directly mutate self/world model state.

### Solver synthesis (declarative + autonomous)

10 default solver families ship as declarative specs that compile deterministically. Autonomous synthesis (gap → spec → candidate) supports 10 candidate states with 4 daily quotas (1000 / 200 / 50 / 20). Cold solver gates: 50 use_count, 3600 s shadow observation, 0 critical regressions. Final approval requires explicit `human_approval_id`.

### Builder + mentor lanes

Worktree allocator, request/result packs, session forge (build from scratch), repair forge (fix from defect), mentor forge (advisory notes only). Mentor notes carry `lifecycle_status: advisory` and never claim authority.

### Memory tiering

Hot / warm / cold / glacier with an access pattern tracker that counts uses but never rewrites meaning. Pinning engine auto-pins foundational entries. Demoting a pinned entry to cold or glacier raises `TierViolation`. Invariants are extracted BEFORE deep tiering.

### Real hex runtime topology

7 modules across 8 cells (general, thermal, energy, safety, seasonal, math, system, learning). 4 live states, 4 subdivision states. Subdivision is shadow-first: new children always start at `live_state="shadow_only"`.

### Promotion ladder

14 stages from `curiosity` to `full_runtime`, with `archived` reachable from anywhere. The 4 runtime stages (`post_campaign_runtime_candidate`, `canary_cell`, `limited_runtime`, `full_runtime`) require a non-empty `human_approval_id`. `detect_bypass()` flags multi-step skips. Rollback engine demands the same human id when rolling back from a runtime stage.

### Proposal compiler

Meta-proposal → 8-artifact engineering bundle: `bundle.json`, `pr_draft.md`, `patch_skeleton.diff`, `affected_files.json`, `test_spec.json`, `rollout_plan.json`, `rollback_plan.json`, `acceptance_criteria.json`, `review_checklist.md`. Const-true `no_main_branch_auto_merge` and `no_runtime_mutation` enforced via source-grep tests. Real evidence at `docs/runs/phase9_pipeline_demo_compiled/` (Session D meta-proposal `4116420fed0a` → `bundle_9b273467f0`).

### Local model distillation — SAFE SCAFFOLD ONLY

5 modules: `local_model_manager`, `fine_tune_pipeline`, `inference_router`, `model_evaluator`, `drift_detector`. Hard rules:

- No `torch` / `transformers` / `ollama` / `openai` / `anthropic` / `requests` / `httpx` / `subprocess` imports
- `FineTunePipeline.execute()` raises by design
- `InferenceRouter` refuses 6 critical task kinds (foundational axiom authoring, runtime mutation proposal, main-branch merge decision, human-facing calibration claim, self-model promotion, world-model promotion)
- Local routing default-disabled; even when enabled returns `local_model_shadow` with `external_provider` fallback
- Lifecycle states: `shadow_only`, `advisory`, `retired` — **no production status by design**

### Cross-capsule observer

`CapsuleSignalSummary` requires `redacted=True`. Observer consumes only redacted summaries and emits `CrossCapsuleObservation` with const-true `redacted` + `advisory_only` + `no_raw_data_leakage`. 4 observable pattern kinds: `recurring_oscillation`, `recurring_blind_spot_class`, `recurring_proposal_bottleneck`, `recurring_contradiction_pattern`.

## What's intentionally NOT in this release

- **Phase 8.5 producer subsystems** (`vector-chaos`, `curiosity-organ`, `self-model-layer`, `dream-curriculum`, `hive-proposes`) — ship as separate PRs after this release. Phase 9 ships the IR adapter contracts; producers ride on follow-up PRs.
- **Atomic runtime flip** — separate Prompt 2 session, gated on signed approval artifact.
- **6 high-risk variants** documented as deferred to Phase 12+ (parallel ensembles, predictive preheating, unbounded micro-learning, canary auto-promotion, advanced local model escalation, generative memory compression).

## License

- LICENSE-BUSL.txt **Change Date harmonized to 2030-03-19** (matches phase8.5/3c67c95)
- SPDX-only convention enforced; embedded markers stripped from 33 source files
- Phase 9 SPDX coverage: 147/147 source files (107 BUSL-1.1 crown-jewel + 40 Apache-2.0 tools/tests/UI)

## Upgrade notes

This is an additive release. The existing 3.5.7 runtime path is unchanged. No migration is required. The new fabric is observable but only consulted when explicitly invoked — the atomic runtime flip that would make it the authoritative live path is a separate, future, human-gated session.

## Acknowledgments

Built by **Jani Korpi** ([Ahkerat Mehilaiset](https://github.com/Ahkeratmehilaiset)) with Claude Code. The 16-phase scaffold was built across multiple parallel session worktrees (master + Sessions A/B/C/D/R7.5) and consolidated under PR #51.
