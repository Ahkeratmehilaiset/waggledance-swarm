# WaggleDance

> A local-first cognitive operating system. Deterministic solver-first routing, builder/mentor lanes for capability growth, vector provenance with identity anchors, multimodal ingestion, and a Reality View as the operator surface — with safe review and human-gated promotion separating proposal from runtime change.

[![Tests](https://img.shields.io/badge/tests-657%20Phase%209%20%2B%2059%20Phase%2010%20targeted-brightgreen)]()
[![CI](https://github.com/Ahkeratmehilaiset/waggledance-swarm/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Ahkeratmehilaiset/waggledance-swarm/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)]()
[![License](https://img.shields.io/badge/license-Apache%202.0%20%2B%20BUSL%201.1-orange)]()
[![Version](https://img.shields.io/badge/version-3.6.0%20%2B%20P10%20substrate%20%2B%20P11%20autogrowth%20%2B%20P12%20self--starting%20%2B%20P13%20runtime--harvest-blue)]()

## What this is

A locally-running cognitive runtime that decides things deterministically when it can, and consults learning components only when it has to.

- **Solver-first routing.** Every query is dispatched to a deterministic solver before any learned model or LLM is consulted. The solver layer is authoritative; everything above it is advisory.
- **Builder and mentor lanes (Phase 9).** Two safe lanes for the system to grow new capability without touching live runtime: a builder lane that drafts proposals, a mentor lane that supplies advisory context. Both produce review artifacts; neither auto-applies.
- **Vector provenance with identity anchors.** Every ingested artifact carries a 4-level dedup signature (exact / semantic / sibling / contradiction-or-extension), an identity anchor, and an append-only provenance graph.
- **Multimodal ingestion.** A single ingestion contract accepts files, folders, FAISS databases, and live streams in copy / link / stream modes.
- **Capsule-aware deployment.** Use cases are represented as capsules (factory, cottage, home, gadget, personal, research) so the core treats them as data, not hardcoded business logic.
- **Reality View.** A 11-panel structured view of the system's current state — never fabricates values; missing data shows up as `available=false` with a structured rationale, not as zero or as a guess.
- **Promotion ladder with human gate.** A 14-stage ladder from curiosity through tension, dream target, meta-proposal, human review, post-campaign runtime candidate, canary cell, limited runtime, full runtime. Four runtime stages require an explicit `human_approval_id`. No auto-promotion.
- **Provider plane.** Multi-provider routing (Claude, GPT, local Ollama, etc.) gated by a 6-layer trust gate before any provider response can influence self-model or world-model state.

## What this is not

To keep the project honest:

- **Not a chatbot wrapper.** A language model is consulted only when solvers and specialist models cannot resolve the query.
- **Not auto-merging or auto-deploying.** No code path on a release branch performs `git push origin main`, `merge_to_main(...)`, or `promote_to_runtime(...)`. The atomic runtime flip is a separate, explicitly human-gated session.
- **Not pretending the producer-side is on main.** The Phase 8.5 producer subsystems (curiosity organ, self-model snapshot, dream curriculum, hive proposes) are real but ship as separate PRs after the Phase 9 scaffold lands. The Phase 9 release contains the contracts and consumers, not the producers.
- **Not finished.** This release lands the autonomy fabric scaffold + 16 phases of architecture, all green. Generative memory compression, parallel provider ensembles, predictive cache preheating, and a few other speculative variants are explicitly deferred to Phase 12+ with documented blockers.

## Architecture

```
Query → Solver Router → Solver Engines (Layer 3, authoritative)
                     → Specialist Models (Layer 2, sklearn, 14 models with canary lifecycle)
                     → LLM fallback (Layer 1, Ollama or stub)
                     ↓
                     Verifier (checks against World Model)
                     ↓
                     CaseTrajectory → MAGMA Audit Trail (Audit / Replay / Overlay / Provenance / Trust)
                     ↓
                     Night Learning  /  Dream Mode (counterfactual sims)
```

The runtime is built around a hexagonal layout: `core/` is the domain, `adapters/http/routes/` and `adapters/llm/` are ports, `bootstrap/` is the DI container, `application/` holds DTOs/services. Hex-cell FAISS retrieval is keyed by `core/hex_cell_topology` — solvers organize into 8 cells (`general`, `thermal`, `energy`, `safety`, `seasonal`, `math`, `system`, `learning`).

### Layers

| Layer | Role | Examples |
|-------|------|----------|
| **3 — Authoritative** | Decides | Solver engines, World Model, Policy Engine, Verifier |
| **2 — Learned** | Adapts | 14 specialist models with canary lifecycle |
| **1 — Fallback** | Explains | LLM — only when solvers and specialists cannot handle it |
| **1b — Optional** | Gemma 4 | Optional dual-tier Gemma 4 profiles: fast (e4b) for general fallback, heavy (26b) for hard reasoning |

## Phase 13 — Runtime-integrated harvest and capability-aware uptake

After Phase 12's self-starting loop, Phase 13 connects the loop to the **real runtime seam** and adds **capability-aware** dispatch so harvested structure stays useful at scale.

What is real now:

* **Runtime query router (`runtime_query_router.py`).** A real seam: callers invoke `RuntimeQueryRouter.route(query)` after their authoritative built-in solvers. The router dispatches to auto-promoted solvers (capability-aware first, family-FIFO fallback) and, on miss, automatically emits a bounded, deduped `runtime_gap_signal`. No human trigger.
* **Capability-aware dispatch (schema v4).** New `solver_capability_features` table indexed on `(family_kind, feature_name, feature_value)`. Each promoted solver carries a small structured feature set (e.g. `scalar_unit_conversion` → `{from_unit, to_unit}`; `lookup_table` → `{domain, default_present}`; `threshold_rule` → `{subject, operator}`). Runtime queries match by structured equality, not by insertion order.
* **Canonical seed library (`low_risk_seed_library.py`).** 68 curated seeds across all six families and 8 hex cells (20 unit conversions, 12 lookup tables, 12 threshold rules, 8 interval bands, 8 linear forms, 8 interpolation curves). Local-first; no provider call in the common path.
* **End-to-end before/after proof.** `tools/run_runtime_harvest_proof.py` routes 68 structured runtime queries through the real router, then re-routes the same queries after one harvest cycle. **Pass 1: 0 served, 68 misses, 68 signals emitted automatically. Pass 2: 68 served via capability lookup, 0 misses. `provider_jobs` = 0.** Artifact at [`docs/runs/phase13_runtime_harvest_2026_04_30/runtime_harvest_proof.md`](docs/runs/phase13_runtime_harvest_2026_04_30/runtime_harvest_proof.md).
* **Reality View `autonomy_runtime_harvest_kpis` panel.** Surfaces runtime-harvested signal counts split by family and cell, plus the truthful self-starting / teacher-assisted / human-gated split.
* **Inner-loop / outer-loop truth, locked by test.** `tests/autonomy_growth/test_runtime_harvest_proof_smoke.py::test_runtime_harvest_proof_zero_provider_calls` runs the proof and asserts `provider_jobs_total == 0` and `builder_jobs_total == 0`.

What is still NOT real:

* The router is a real seam, but it is exercised by the proof and tests in this PR. Wiring the router into a specific runtime call site (e.g. a hot path inside the autonomy runtime) is a separate session's work.
* High-risk families remain human-gated.
* Stage-2 atomic flip is unchanged. `core/faiss_store.py:26 _DEFAULT_FAISS_DIR=data/faiss/`.
* No real Anthropic / OpenAI HTTP adapters.
* No actuator-side autonomy.
* No consciousness claim.

## Phase 12 — Self-starting local-first autogrowth loop

After Phase 11's closed no-human auto-promotion loop, Phase 12 closes the missing left-hand side: a **self-starting** intake that turns runtime evidence into queued growth intents *without a human trigger*, and a local-first scheduler that drains the queue end-to-end with **zero provider calls** in the inner loop.

What is real now (post-Phase-12):

* **Self-starting gap intake.** `RuntimeGapDetector.record(GapSignal)` writes evidence into `runtime_gap_signals`; `digest_signals_into_intents` aggregates into `growth_intents` and enqueues into `autogrowth_queue` — all in the control plane, no JSON-on-disk.
* **Append-only history mirror.** Every intake / intent / enqueue / promotion event emits a `growth_events` row. The control plane keeps current state; `growth_events` keeps history.
* **Self-starting scheduler.** `AutogrowthScheduler.run_until_idle()` claims queue rows atomically (no double-claim across concurrent schedulers), dispatches each to `LowRiskGrower.grow_from_gap`, records `autogrowth_runs`, and emits growth events. Rejection is terminal (deterministic given the seed).
* **Mass-safe proof at scale.** `tools/run_mass_autogrowth_proof.py` runs the full loop end-to-end, growing **30 deterministic low-risk solvers across all 6 allowlisted families and 8 hex cells** with **0 rejections, 0 errors, 0 provider calls**. Reproducible artifact at [`docs/runs/phase12_self_starting_autogrowth_2026_04_30/autonomy_proof.md`](docs/runs/phase12_self_starting_autogrowth_2026_04_30/autonomy_proof.md).
* **Reality View.** New `autonomy_self_starting_kpis` aggregate panel surfaces queue / intent / promotion counts split by family and cell. The Phase 11 `autonomy_low_risk_kpis` panel still works alongside it; never-fabricate invariant preserved.
* **Inner-loop / outer-loop truth (locked by test).** `tests/autonomy_growth/test_outer_inner_loop_truthful.py::test_mass_autogrowth_zero_provider_calls` runs the mass proof and asserts the resulting `provider_jobs` table is empty. If a future change quietly routes inner-loop common-path growth through a paid provider, the test fails.

What is still NOT real:

* High-risk families (`weighted_aggregation`, `temporal_window_rule`, `structured_field_extractor`, `deterministic_composition_wrapper`) remain human-gated through the Phase 9 promotion ladder.
* The Phase 9 14-stage promotion ladder is unchanged. Runtime-stage promotions still require a real `human_approval_id`.
* The Stage-2 atomic flip is unchanged. `core/faiss_store.py:26 _DEFAULT_FAISS_DIR=data/faiss/`. The Stage-2 RFC still gates that mechanism.
* No real Anthropic / OpenAI HTTP adapters. Only `dry_run_stub` and `claude_code_builder_lane` are exercisable end-to-end. Phase 12 does not change that.
* No actuator / policy / production-runtime-config writes. Auto-promoted low-risk solvers are *consultable*, not *authoritative*.
* No consciousness claim. Self-starting growth is an engineering mechanism — observable, reversible, audit-traceable. See `docs/journal/2026-04-30_autonomy_trajectory_and_gap_analysis.md` for the explicit non-claim.

## Phase 11 — Autonomous low-risk solver growth lane

After v3.6.0 + Phase 10 substrate, Phase 11 lands the first **closed no-human autonomous solver growth lane** for a bounded allowlist of deterministic, side-effect-free families. The lane is bounded by [`docs/architecture/LOW_RISK_AUTOGROWTH_POLICY.md`](docs/architecture/LOW_RISK_AUTOGROWTH_POLICY.md).

What is real now:

* Six allowlisted families can be auto-promoted by the system without a human in the inner loop: `scalar_unit_conversion`, `lookup_table`, `threshold_rule`, `interval_bucket_classifier`, `linear_arithmetic`, `bounded_interpolation`.
* `waggledance/core/autonomy_growth/` ships the closed loop: `LowRiskGrower` (gap → spec → compile), `AutoPromotionEngine` (validate → shadow → I1–I9 invariants → atomic decision write), `LowRiskSolverDispatcher` (runtime executor between built-in solvers and LLM fallback), and `solver_executor.py` (six pure executors).
* Control-plane schema v2 adds five new normalized tables — `family_policies`, `validation_runs`, `shadow_evaluations`, `promotion_decisions`, `autonomy_kpis` — plus a `solver_artifacts` table for the executable compiled form. No JSON-on-disk system-of-record (RULE 10).
* Reality View gains an `autonomy_low_risk_kpis` aggregate panel that surfaces auto-promotions / rejections / rollbacks / dispatcher hits without listing per-solver state at scale. The never-fabricate invariant is preserved: an empty autonomy lane returns `available=false` with `rationale_if_unavailable="no_autonomy_activity_recorded_yet"`.
* `tools/run_autonomy_proof.py` produces a reproducible end-to-end proof artifact at [`docs/runs/phase11_autogrowth_2026_04_29/autonomy_proof.md`](docs/runs/phase11_autogrowth_2026_04_29/autonomy_proof.md) and a machine-readable [`autonomy_proof.json`](docs/runs/phase11_autogrowth_2026_04_29/autonomy_proof.json).

What is **not** real:

* High-risk families (`weighted_aggregation`, `temporal_window_rule`, `structured_field_extractor`, `deterministic_composition_wrapper`) remain human-gated through the existing Phase 9 promotion ladder.
* The 14-stage Phase 9 promotion ladder is unchanged — runtime-stage promotions still require a real `human_approval_id`. Auto-promotion in this lane is a *separate status flag* (`solvers.status='auto_promoted'`), not a runtime-stage promotion.
* The Stage-2 atomic flip is unchanged. `core/faiss_store.py:26 _DEFAULT_FAISS_DIR=data/faiss/` is the runtime read path until subsystems opt into `PathResolver`. The mechanism is specified in [`docs/architecture/STAGE2_CUTOVER_RFC.md`](docs/architecture/STAGE2_CUTOVER_RFC.md) and remains unexecuted.
* Real Anthropic / OpenAI HTTP adapters are still follow-up work. Only `dry_run_stub` and `claude_code_builder_lane` are exercisable end-to-end today; Phase 11 does not change that.
* No actuator-side autonomy. Auto-promoted low-risk solvers are *consultable*, not *authoritative*; built-in solvers retain precedence.

## Phase 10 — Foundation, Truth, Builder Lane (substrate landed on main)

After v3.6.0 shipped the Phase 9 autonomy fabric scaffold, Phase 10 added the substrate for tens of thousands of solvers, made the Claude Code / Anthropic / GPT / local-model lanes first-class, and tightened truthfulness across docs and Reality View.

Phase 10 substrate landed on `main` via [PR #54](https://github.com/Ahkeratmehilaiset/waggledance-swarm/pull/54) — squash-merged 2026-04-28T12:14:15Z as commit `08b7e8c`. No new SemVer tag was minted because Phase 10 adds substrate, not runtime hot-path behaviour change. A future runtime-affecting release picks the next version; an optional `v3.6.1-substrate` prerelease tag may be added when post-merge truth/governance are clean.

* **Storage substrate.** A 16-table SQLite control plane (`waggledance/core/storage/`) that owns *current state* of solvers, families, capabilities, vector shards, provider/builder jobs, promotion ladder, runtime path bindings. MAGMA still owns *history*; FAISS / Chroma still own *vector content*. A new `PathResolver` is the seam future cutovers can use without code changes — it is drop-in compatible with the legacy `_DEFAULT_FAISS_DIR` so nothing in the runtime moves until a subsystem opts in.
* **Provider plane execution layer.** `waggledance/core/providers/` adds JSON-schema-validated request/response dispatch on top of the Phase 9 routing scaffold. `ClaudeCodeBuilder` is the only authorised subprocess (isolated worktree, bounded timeout, JSONL invocation log, dry-run fallback when CLI absent). The mentor-output advisory boundary is enforced at the API surface: mentor notes are IR `learning_suggestion` objects with `lifecycle_status='advisory'`, never directly mutating runtime.
* **Solver bootstrap orchestrator.** `solver_synthesis/solver_bootstrap.py` implements the U1→U3 escalation rule on top of the Phase 9 declarative pipeline + Phase 10 LLM generator (provider-plane backed) + cold/shadow throttler. Family-first growth: high-confidence gaps go declarative; low-confidence gaps escalate to free-form via Claude Code or Anthropic.
* **Scale-aware Reality View.** `ui/hologram/scale_aware_aggregator.py` aggregates per-family rollups, per-cell counts, and queue summaries from the control plane, so the Reality View never claims "one node per solver" at 10k+ scale. The Phase 9 11-panel structure and never-fabricate invariant are unchanged.
* **Storage / cutover truth audit.** `docs/journal/2026-04-28_storage_runtime_truth.md` and `2026-04-28_cutover_model_classification.md` ground the runtime claims with file:line citations. The v3.6.0 atomic flip is formally classified `MODEL_C_NOOP_ALREADY_COMPLETE`; future Stage-2 flip is `MODEL_D_AMBIGUOUS` until an RFC defines the mechanism.

What Phase 10 explicitly does **not** do: execute the runtime cutover, ship real Anthropic/OpenAI HTTP adapters, replace any Phase 9 module, or claim the autonomy runtime emits vector events at runtime (offline tools do; the autonomy runtime does not yet). The future Stage-2 cutover mechanism is specified in [`docs/architecture/STAGE2_CUTOVER_RFC.md`](docs/architecture/STAGE2_CUTOVER_RFC.md) and remains explicitly *not executed* until a fresh one-shot human approval is collected against that RFC.

## Phase 9 — Autonomy Fabric (v3.6.0 release)

The 16 phases of the autonomy fabric ship in this release as a self-contained scaffold:

| Phase | Module | Purpose |
|---|---|---|
| F | `waggledance/core/autonomy/` | Always-on cognitive kernel (10 sub-components: kernel state, governor, mission queue, budget engine, policy core, action gate, attention allocator, background scheduler, micro-learning lane, circuit breaker) |
| G | `core/ir/` + `core/capsules/` | Cognition IR + Capsule Registry with blast-radius enforcement |
| H | `core/vector_identity/` + `core/ingestion/` | Vector provenance graph, identity anchors, universal ingestion |
| I | `core/world_model/` | Calibrated world model, drift detection |
| P | `ui/hologram/reality_view.py` | 11-panel Reality View (never-fabricate invariant) |
| V | `core/conversation/` + `core/identity/` | Presence log, meta-dialogue, forbidden-pattern scanning |
| J | `core/provider_plane/` + `core/api_distillation/` | Multi-provider routing + 6-layer distillation trust gate |
| U1 | `core/solver_synthesis/` (declarative) | 10 default solver families |
| U2 | `core/builder_lane/` | Builder, repair forge, mentor forge |
| U3 | `core/solver_synthesis/` (gap-driven) | Autonomous solver synthesis with cold-shadow throttling |
| L | `core/memory_tiers/` | Hot/warm/cold/glacier with pinning + invariant extraction |
| K | `core/hex_topology/` | Real hex runtime topology (4 live states, 4 subdivision states) |
| M | `core/promotion/` | 14-stage promotion ladder (4 runtime stages require human approval) |
| O | `core/proposal_compiler/` | Meta-proposal → engineering bundle (patch skeleton, affected files, test spec, rollout plan, rollback plan, acceptance criteria, review checklist, PR draft) |
| N | `core/local_intelligence/` | Local model distillation **safe scaffold** (advisory-only, 6 critical task kinds refused, no auto-promotion) |
| Q | `core/cross_capsule/` | Cross-capsule observer (redacted summaries in, redacted observations out) |

All Phase 9 core modules are BUSL-1.1 protected. Tools, tests, and UI compatibility code are Apache 2.0. Schemas are public-interface artifacts.

See [`docs/architecture/PHASE_9_ROADMAP.md`](docs/architecture/PHASE_9_ROADMAP.md) for the full navigation surface.

## Builder and Mentor Lanes

Two complementary safe lanes for capability growth (`waggledance/core/builder_lane/`):

- **Builder lane.** Allocates a worktree, packages a request (`builder_request_pack`), and hands it to a session forge or repair forge. Returns a result pack. Never modifies the live runtime path.
- **Mentor lane.** Produces advisory context (`mentor_forge`) — notes, alternatives, hints — that travel as advisory IR (`lifecycle_status: advisory`). The lane never claims authority; it just informs the proposal compiler.

Subprocess invocation is gated for human review by default. The CLI emits an `advisory_only` outcome unless explicitly run with operator approval.

## Memory and Identity

- **Vector identity** (`core/vector_identity/`): every persistent artifact has a 4-level dedup signature, an identity anchor (foundational anchors enter a candidate state first), and a chained provenance graph.
- **Memory tiers** (`core/memory_tiers/`): hot / warm / cold / glacier with an access pattern tracker that counts uses but never rewrites meaning. Pinning engine auto-pins foundational entries; demoting a pinned entry to cold or glacier raises `TierViolation`.
- **Invariant extractor** runs BEFORE deep tiering: extracts constraints, schemas, and relations so they survive demotion.
- **Self-model layer** (Phase 8.5, ships as separate follow-up PR): scorecards, blind spots, workspace tensions, attention focus.

## Capsules and deployment

The system is configured by capsule manifests, not hardcoded business logic. Active capsules:

- `factory_v1` — industrial telemetry, OEE/SPC, predictive maintenance
- `cottage_v1` — off-grid heating, frost protection, energy management
- `home_v1` — comfort automation, safety, energy optimization
- `gadget_v1` — edge / IoT (RPi, Jetson)
- `personal_v1` — single-user assistant
- `research_v1` — exploratory experiments

Each capsule declares its blast radius, rate limits, sensors, and forbidden actions. The core treats capsule context as a typed value (`capsule_context`), never as branching business rules.

## Quick start

### Docker (recommended)

```bash
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
docker compose up -d
```

Dashboard: http://localhost:8000 | Reality View: http://localhost:8000/hologram

### Native

```bash
pip install -r requirements.txt

# Requires Ollama running locally (ollama serve)
python start_waggledance.py

# Stub mode — no Ollama needed
python start_waggledance.py --stub

# Capsule preset
python start_waggledance.py --preset=cottage-full
```

## Reality View

The `/hologram` page renders an 11-panel structured operator view. Each panel is one of:

- `available=true` with real items, OR
- `available=false` with a structured `rationale_if_unavailable` string (e.g., `"vector_graph snapshot missing"`)

The never-fabricate invariant means a missing data source is reported as missing — not papered over with default values or zero. See `waggledance/ui/hologram/reality_view.py`.

A real evidence render against Session B `self_model_snapshot` is committed at [`docs/runs/phase9_reality_view_render.json`](docs/runs/phase9_reality_view_render.json) — 5/11 panels populated, 6/11 honestly unavailable.

## MAGMA Memory Architecture

| Layer | Component | Role |
|-------|-----------|------|
| **L1** | AuditLog | Append-only event log — goals, plans, actions, verifications |
| **L2** | ReplayEngine | Mission-level replay with chronological step reconstruction |
| **L3** | MemoryOverlay | Filtered views by profile, mission, entity |
| **L4** | Provenance | 9-tier source tracking (verifier → observed → solver → rule → stats → case → reflection → LLM → simulated) |
| **L5** | TrustEngine | Multi-dimensional scoring for agents, capabilities, solvers, routes, specialists |

## Promotion ladder (the human gate)

The 14-stage promotion ladder at `waggledance/core/promotion/`:

```
curiosity → tension → dream_target → meta_proposal → human_review
  → post_campaign_runtime_candidate → canary_cell → limited_runtime → full_runtime
                                              ↘ archived (allowed from anywhere)
```

The four runtime stages (post-campaign-runtime-candidate / canary-cell / limited-runtime / full-runtime) require a non-empty `human_approval_id` of the form `human:<reviewer>:<utc-iso>`. The promotion engine refuses transitions that don't carry one. `detect_bypass()` flags multi-step skips. `rollback_engine` requires the same human id when rolling back from a runtime stage.

## Final atomic runtime flip — separate session

The Phase 9 release lands the scaffold. The actual atomic runtime flip — pointing the live runtime read path at the new code — is **not** part of this PR. It runs as a separate prompt (`Prompt 2`, see [`docs/architecture/PROMPT_2_INPUTS_AND_CONTRACTS.md`](docs/architecture/PROMPT_2_INPUTS_AND_CONTRACTS.md)) after:

1. all Phase 8.5 follow-up PRs land on main
2. the 400h gauntlet campaign is finished or frozen
3. a signed approval artifact has been authored by a human reviewer

The flip is a fast-forward `git push <release_branch>:main` with head-SHA protection, never a force-push.

## API

REST + WebSocket on port 8000. Key groups:

| Group | Examples |
|-------|----------|
| Core | `POST /api/chat`, `GET /api/status`, `GET /api/heartbeat` |
| Ops | `GET /api/ops` — live FlexHW tier + AutoThrottle telemetry |
| Autonomy | `/api/autonomy/status`, `/api/autonomy/kpis`, `/api/autonomy/learning/run` |
| Hologram / Reality View | `GET /api/hologram/state`, `GET /hologram` |
| Storage | `GET /api/storage/health`, `POST /api/storage/wal-checkpoint` |
| Introspection | `/api/magma/*`, `/api/graph/*`, `/api/trust/*`, `/api/cross-agent/*`, `/api/analytics/*` |
| Profiles | `GET /api/profiles` — `{active, configured, restart_required}` |
| Feeds | `GET /api/feeds` — config-based sources with per-source freshness |
| Learning | `/api/learning/state-machine`, `/api/capabilities/state` |
| Sensors | `/api/sensors`, `/api/sensors/home`, `/api/sensors/camera/events` |
| Hybrid | `/api/hybrid/status`, `/api/hybrid/topology`, `/api/hybrid/cells` — hex-cell FAISS retrieval |

WebSocket at `ws://localhost:8000/ws` for real-time brain updates, chat streaming, alerts.

See [`docs/API.md`](docs/API.md) for the full reference.

## Security

- **Auth** — HttpOnly session cookie (SameSite=Strict, 1 h TTL) for the browser; Bearer token for cURL/scripts/CI. API key auto-generated on first start.
- **No browser-visible secrets** — master key never appears in served HTML, inline JS, localStorage, or sessionStorage.
- **No frontend Bearer construction** — all browser fetches use `credentials: 'same-origin'`.
- **No `?token=` in frontend WebSocket** — browser WS connects clean; token parameter is accepted server-side for scripts only.
- **No eval()** — AST-based whitelist expression evaluator (`core/safe_eval.py`).
- **Safe Action Bus** — all write operations go through policy → risk → approval chain.
- **OOM protection** — ResourceGuard with adaptive throttling and emergency GC.
- **MQTT TLS** — enabled by default (port 8883).

## Phase 8 — Honeycomb Solver Scaling (still scaffolding)

Phase 8 is the substrate Phase 9 builds on. It adds planning, hashing, and gating tools for safe solver-library growth without flipping any runtime switch:

- `tools/cell_manifest.py` — deterministic per-cell state cards
- `waggledance/core/learning/solver_hash.py` — strict `solver_hash()` + dedup scanner
- `schemas/solver_proposal.schema.json` + `tools/propose_solver.py` — 12-gate quality review, never auto-merges
- `waggledance/core/learning/composition_graph.py` — typed DAG over the existing library
- `tools/run_honeycomb_400h_campaign.py` — segment-aware campaign scaffolding; never auto-starts without `--confirm-start`

Design: [`docs/architecture/HONEYCOMB_SOLVER_SCALING.md`](docs/architecture/HONEYCOMB_SOLVER_SCALING.md).

## Testing

```bash
# Phase 9 targeted suite (~7 s, 657 tests)
python -m pytest tests/test_phase9_*.py -q

# Full suite
python -m pytest -q

# Subsystems
python -m pytest tests/autonomy/ -v
python -m pytest tests/contracts/ -v
python -m pytest tests/continuity/ -v
```

The Phase 9 GLOBAL PROPERTY tests at `tests/test_phase9_global_properties.py` enforce: no silent failures, no auto-enactment to main/live runtime, no constitution self-mutation, no foundational auto-promotion without human approval, no capsule blast-radius leakage, deterministic builder request/result IDs, no absolute path leakage, no secret literals, domain-neutrality.

## Why the name "WaggleDance"?

The name comes from honeybee waggle dances — a real-world example of a collective intelligence system where a single discovery doesn't become a decision until peer feedback validates it. A scout's dance encodes direction (angle), distance (duration), and quality (vigor); experienced nestmates touch the dancer with antennae and provide live feedback; a stop signal can shut the dance down entirely.

The codebase historically used bee/hive metaphors throughout. New code (Phase 9 onward) is **domain-neutral**: terms like Cognitive Fabric, Reality View, Capsule, Cell, Runtime Topology, Provenance, Distillation, Builder Lane, and Mentor Lane replace bee/swarm/honeycomb/factory metaphors in core modules. Legacy paths and product names remain for compatibility.

## License

**Dual-licensed:**

| Component | License | File |
|-----------|---------|------|
| Open core (infrastructure, adapters, tests, API, tools) | Apache 2.0 | [`LICENSE`](LICENSE) |
| Protected modules (autonomy fabric, dream mode, consolidator, meta-optimizer, projections, MAGMA core) | BUSL 1.1 | [`LICENSE-BUSL.txt`](LICENSE-BUSL.txt) |

Protected module list: [`LICENSE-CORE.md`](LICENSE-CORE.md). Phase 9 SPDX coverage: 147/147 source files tagged (107 BUSL-1.1 + 40 Apache-2.0).

BUSL change date: **2030-03-19** — protected modules become Apache 2.0 automatically on this date.

**Personal non-commercial use of protected modules is permitted.**
Commercial licensing: see [`COMMERCIAL-USE.md`](COMMERCIAL-USE.md) or contact janikorpi@hotmail.com.

## Credits

Built by **Jani Korpi** ([Ahkerat Mehilaiset](https://github.com/Ahkeratmehilaiset), Helsinki) with [Claude Code](https://claude.ai/claude-code).

---

*WaggleDance — Local. Auditable. Human-gated.*
