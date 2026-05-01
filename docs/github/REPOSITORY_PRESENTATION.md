# Repository Presentation Surface — WaggleDance

This file is the canonical source for the repository's external-facing presentation: GitHub "About" text, topic tags, and the short summary that should sit at the top of `README.md`. It is written for an external GitHub reader who has never used the project.

The text in this file is *prepared*, not *applied*. Setting GitHub repo metadata (description, topics, social preview) is an explicit operator action; this PR does not invoke `gh repo edit`.

## GitHub "About" — short description (≤ 160 characters)

> Local-first deterministic-solver runtime with a bounded six-family auto-growth lane. Alpha. No cloud, no provider in the inner loop.

(159 characters, including spaces.)

## Suggested topics / tags

* `python`
* `runtime`
* `solver`
* `deterministic`
* `autonomy`
* `local-first`
* `sqlite`
* `audit`
* `alpha`

Avoid: `ai`, `agi`, `conscious`, `intelligent` — these are inaccurate or marketing-shaped for what this repository actually ships.

## One-paragraph external summary

WaggleDance is a Python runtime that dispatches structured queries through deterministic solvers first and falls back to learned components only when no solver matches. It includes a bounded auto-growth lane that promotes new low-risk deterministic solvers from runtime evidence — currently scoped to six side-effect-free families (unit conversion, lookup tables, threshold rules, interval bands, linear forms, bounded interpolation). The auto-growth common path runs with zero provider calls and writes to a SQLite control plane plus an append-only event log. High-risk growth, runtime-stage promotion, actuator writes, and the Stage-2 atomic flip remain human-gated.

## What is real now

* **Solver-first reasoning router** (`waggledance/core/reasoning/solver_router.py`).
* **Phase 11–14 autonomy lane** (`waggledance/core/autonomy_growth/`):
  * deterministic compiler + executor for six allowlisted families,
  * validation + shadow-evaluation auto-promotion engine,
  * runtime gap intake + autogrowth scheduler,
  * capability-aware dispatch (schema v4 `solver_capability_features`),
  * in-memory `WarmCapabilityIndex` + `ParsedArtifactCache` + `BufferedSignalSink` (Phase 14).
* **Phase 15 automatic runtime hint extractor** (`waggledance/core/autonomy_growth/runtime_hint_extractor.py`) wired into `AutonomyRuntime.handle_query` — production query handler derives autonomy hints from the natural `context["structured_request"]` payload; no manual injection.
* **Reproducible proofs** under `docs/runs/phase11..15_*/`. Latest: 98-seed corpus through `AutonomyRuntime.handle_query` with 0 provider calls.

## What is alpha

* Auto-growth lane — works end-to-end, but only on the six allowlisted families.
* Capability-aware dispatch — works for the per-family feature shapes defined in `family_features.py`. Richer routing dimensions (per-cell, per-capsule) are future work.
* Hot-path cache — works in-process; cache invalidation on solver promotion / deactivation is best-effort.
* Self-state snapshot + episodic continuity surfaces are deferred to Phase 16.

## What is not implemented

* No real Anthropic / OpenAI HTTP adapters. Only `dry_run_stub` and `claude_code_builder_lane` are exercisable end-to-end. The provider routing scaffold exists; the network adapters do not.
* No actuator-side autonomy. Auto-promoted solvers are *consultable*, not *authoritative*. Built-in solvers retain precedence; LLM fallback still exists.
* No Stage-2 atomic flip. `core/faiss_store.py:26 _DEFAULT_FAISS_DIR=data/faiss/` is the runtime read path. Migration to a future `data/vector/` layout is specified in `docs/architecture/STAGE2_CUTOVER_RFC.md` but not executed.
* No federation. Single-process scope.
* No high-risk family auto-promotion. The four excluded families (`weighted_aggregation`, `temporal_window_rule`, `structured_field_extractor`, `deterministic_composition_wrapper`) flow through the existing Phase 9 14-stage promotion ladder which requires a human approval id.
* No production deployment story. Docker is provided for inspection / proof reproduction. See `docs/deployment/DOCKER_QUICKSTART.md`.

## How to reproduce the latest proof

The Phase 15 proof lives at `docs/runs/phase15_runtime_hints_release_2026_05_01/automatic_runtime_hint_proof.md` + `.json`.

```
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
pip install -r requirements.lock.txt
python tools/run_automatic_runtime_hint_proof.py
```

Expected: 98 corpus items, 0 served pre-harvest, 98 promoted, 98 served post-harvest via capability lookup, 0 provider/builder delta.

## How to run locally

```
pip install -r requirements.lock.txt
python -m pytest tests/autonomy_growth/ tests/storage/ tests/ui_hologram/ -q
```

## How Docker is expected to work

```
docker build -t waggledance:phase15-alpha .
docker run --rm waggledance:phase15-alpha python tools/run_automatic_runtime_hint_proof.py
```

See `docs/deployment/DOCKER_QUICKSTART.md` for the full procedure, expected outputs, and known limitations.

## Consciousness and intelligence claims — explicit non-claim

WaggleDance does **not** claim to be conscious, sentient, aware, alive, awake, AGI, or to understand or think in a human-like way. The autonomy mechanisms described here — auto-growth, runtime harvest, capability-aware dispatch, hot-path cache — are engineering primitives. Each maps to a code path, a persisted event, and a regression test. None of them, alone or in combination, constitutes consciousness.

## LLM / provider truth

* The inner growth loop is local-first and deterministic. No LLM is consulted on the warm path, the cold-cache path, or the hint extraction path.
* The outer-loop teacher / builder lane (`claude_code_builder_lane`) is the canonical preference for spec invention when no canonical seed exists. It is exercised through `ClaudeCodeBuilder` (subprocess to `claude` CLI when available; `dry_run_stub` fallback otherwise). It is *not* in the inner loop.
* Anthropic, OpenAI / GPT, and local-model lanes appear in the routing scaffold as architectural peers; concrete HTTP adapters are not implemented.

## Safety / human-gate truth

* The Phase 9 14-stage promotion ladder still gates *runtime-stage* promotions (post-campaign-runtime-candidate, canary-cell, limited-runtime, full-runtime). A real `human_approval_id` is required.
* Auto-promotion in the Phase 11+ lane is a separate `solvers.status='auto_promoted'` flag and only flows through the consult lane between built-ins and LLM fallback. It does not bypass the human gate.
* No autonomy module writes to actuators, policies, or production runtime configuration without explicit human gating.

## How this README/About text is meant to be applied

The text above is recommended copy. Applying it:

* **GitHub "About"** — paste the 159-char description into the repo settings via the GitHub web UI, or run `gh repo edit -d "..." -h "..."` (operator action, not done by this PR).
* **Topics** — add via the same settings panel.
* **README top section** — the `README.md` top section (10 lines, see `docs/release/RELEASE_READINESS.md`) is updated by Phase 15 P9.

## Cross-references

* `README.md` — top-level README with the latest alpha summary.
* `CURRENT_STATUS.md` — chronological phase status.
* `CHANGELOG.md` — per-release notes.
* `docs/release/RELEASE_READINESS.md` — alpha/release tag policy + how this presentation maps to versioning.
* `docs/deployment/DOCKER_QUICKSTART.md` — Docker procedure + tested/not-tested status.
* `docs/architecture/RUNTIME_ENTRYPOINT_TRUTH_MAP.md` — Phase 15 caller inventory.
