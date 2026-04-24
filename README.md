# WaggleDance Swarm AI

> Local-first AI runtime with solver-first routing, self-training specialists, overnight dream learning, and full MAGMA audit trail.

[![Tests](https://img.shields.io/badge/tests-5580%20passing-brightgreen)]()
[![CI](https://github.com/Ahkeratmehilaiset/waggledance-swarm/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Ahkeratmehilaiset/waggledance-swarm/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)]()
[![License](https://img.shields.io/badge/license-Apache%202.0%20%2B%20BUSL%201.1-orange)]()
[![Version](https://img.shields.io/badge/version-3.5.7-blue)]()

## What is this?

Most AI systems make the same mistake: they ask a language model first and hope the answer sounds right.

Nature solved this problem millions of years ago.

In a beehive, a discovery doesn't become a decision because one individual says so. A scout returns to the hive and dances a figure-eight pattern on the vertical surface of the honeycomb — the angle of the straight run encodes direction, duration encodes distance, vigor encodes quality. But the dance is not a monologue. Experienced nestmates follow the dancer closely, touch her with their antennae, and provide feedback in real time. A stop signal can shut the dance down entirely. Only when the message survives collective scrutiny does it become a route worth committing to.

WaggleDance is built on this logic.

It doesn't hand the problem straight to an LLM. It routes it to the right solver first, cross-checks the result through multiple agents, and uses a language model only when it genuinely adds value. Every step leaves an auditable trace. Every decision is justifiable. Every cycle grows the system's own competence.

The figure-eight dance became algorithmic routing. The honeycomb became the MAGMA memory architecture. And the bees' overnight rest became Dream Mode — a safe simulation where the system reviews the day's failures, tests thousands of alternative paths, and returns in the morning sharper than before.

This is not a metaphor. This is an architecture for collective machine intelligence.

## Architecture

```
Query → Language Detection → Solver Router
                                  │
              ┌───────────────────┼───────────────────┐
              ▼                   ▼                   ▼
        Solver Engines      Specialist Models      LLM (Ollama)
        (Layer 3)           (Layer 2, sklearn)     (Layer 1, fallback)
              │                   │                   │
              └───────────────────┼───────────────────┘
                                  ▼
                            Verifier
                            (checks against world model)
                                  │
                   ┌──────────────┤
                   ▼              ▼
             Chat Funnel    Autonomy Funnel
             (Q&A cases)    (full lifecycle)
                   └──────────────┤
                                  ▼
                        CaseTrajectory → MAGMA Audit Trail
                                              │
                                  ┌───────────┴───────────┐
                                  ▼                       ▼
                           Night Learning           Dream Mode
                           (train specialists)      (counterfactual sims)
```

### Layers

| Layer | Role | Examples |
|-------|------|----------|
| **3 — Authoritative** | Decides | Solver engines, World Model, Policy Engine, Verifier |
| **2 — Learned** | Adapts | 14 specialist models with canary lifecycle |
| **1 — Fallback** | Explains | LLM — only when solvers and specialists cannot handle it |
| **1b — Optional** | Gemma 4 | Optional dual-tier Gemma 4 profiles: fast (e4b) for general fallback, heavy (26b) for hard reasoning |

### Source Layout

```
waggledance/
  core/
    autonomy/        Runtime, resource kernel, lifecycle, attention budget
    world/           World model, entity registry, epistemic uncertainty
    reasoning/       Solver router, verifier
    learning/        Dream mode, consolidator, night pipeline, quality gate
    specialist_models/  Trainer, meta-optimizer, model store (14 sklearn models)
    goals/           Goal engine, motives, mission store
    planning/        Planner
    policy/          Constitution, risk scoring, policy engine
    actions/         Safe Action Bus (deny-by-default)
    capabilities/    Registry, selector
    projections/     Narrative, introspection, autobiographical (read-only)
    magma/           Audit, provenance, replay, trust, confidence decay
    hex_cell_topology  Logical cell overlay for hybrid FAISS retrieval
    domain/          CaseTrajectory, Goal, WorldSnapshot dataclasses
  adapters/
    http/routes/     Hexagonal routes — chat, auth, hologram, magma, graph, trust, ops
    llm/             Ollama adapter
    memory/          ChromaDB, FAISS
    sensors/         MQTT, camera, audio
    config/          YAML bridge, settings loader
  application/       Services, DTOs
  bootstrap/         DI container, capability loader
```

## Quick Start

### Docker (recommended)

```bash
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
docker compose up -d
```

Dashboard: http://localhost:8000 | Hologram: http://localhost:8000/hologram

### Native

```bash
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
pip install -r requirements.txt

# Requires Ollama running locally (ollama serve)
python start_waggledance.py

# Stub mode — no Ollama needed
python start_waggledance.py --stub
```

### Presets

```bash
python start_waggledance.py --preset=cottage-full
python start_waggledance.py --preset=factory-production
```

## Deployment Profiles

| Profile | Target | Description |
|---------|--------|-------------|
| **GADGET** | Edge / IoT (RPi, Jetson) | Minimal — sensor calibration, battery optimization |
| **COTTAGE** | Off-grid property (Mini-PC) | Heating control, frost protection, energy management |
| **HOME** | Smart home (Desktop, NAS) | Comfort automation, safety, energy optimization |
| **FACTORY** | Industrial (Server, GPU) | OEE, SPC, predictive maintenance, ISO audit trail |

Profile controls: agent limits, solver budgets, learning frequency, feed selection, attention allocation.

## MAGMA Memory Architecture

| Layer | Component | Role |
|-------|-----------|------|
| **L1** | AuditLog | Append-only event log — goals, plans, actions, verifications |
| **L2** | ReplayEngine | Mission-level replay with chronological step reconstruction |
| **L3** | MemoryOverlay | Filtered views by profile, mission, entity |
| **L4** | Provenance | 9-tier source tracking (verifier → observed → solver → rule → stats → case → reflection → LLM → simulated) |
| **L5** | TrustEngine | Multi-dimensional scoring for agents, capabilities, solvers, routes, specialists |

## Hologram Brain

The `/hologram` page renders a real-time 3D visualization with 32 nodes across 4 concentric rings:

| Ring | Nodes | Glow Semantic |
|------|------:|---------------|
| Core cognition | 10 | Utilization / current load |
| MAGMA audit | 5 | Throughput / volume |
| System | 8 | Health / availability |
| Learning | 9 | Lifecycle activity |

Each node carries `node_meta`: state (7-value enum), device, freshness, source class, quality.
Docked panel with 8 tabs + Chat. Bilingual FI/EN.

- **Chat** — focus guard prevents input reset during polling refresh
- **Profile selector** — shows the `configured` profile from `settings.yaml`; restart-only behavior with persistent hint when runtime differs
- **Feeds** — per-source freshness with separate stale thresholds (30 s telemetry, 1800 s feeds), truthful source visibility
- **Ops tab** — live FlexHW tier and AutoThrottle telemetry from `/api/ops`

## Current Status

| Metric | Value |
|--------|-------|
| Version | v3.5.7 (Honest Hologram Release) |
| Architecture | Hexagonal — DI container, port/adapter, single-product |
| Runtime | ElasticScaler + AdaptiveThrottle + ResourceGuard via DI |
| Specialist models | 14 (real sklearn training, canary lifecycle) |
| Tests | 5817 passing, 1 pre-existing failure (documented) |
| Production validated | 12 h soak — 358/358 ticks green, 0 restarts |
| UI hardening | 477 Playwright queries (7 buckets), 0 XSS, 0 DOM breaks, 30 min soak stable |
| Cutover | Full autonomy mode enabled |

### Phase 8 — Honeycomb Solver Scaling *(planned / scaffolding / experimental)*

Phase 8 is the scaffolding layer for safe solver-library growth. It adds
planning, hashing, and gating tools; it does **not** flip any runtime switch.
Active work happens on branch `phase8/honeycomb-solver-scaling-foundation`.

- `tools/cell_manifest.py` — deterministic per-cell state cards for the
  teacher pipeline (one cell per session, never the global library).
- `waggledance/core/learning/solver_hash.py` — strict `solver_hash()` + legacy
  `canonical_hash()` with a dedup scanner (`tools/solver_dedupe.py`).
- `schemas/solver_proposal.schema.json` + `docs/prompts/cell_teacher_prompt.md`
  — machine-checkable contract for any teacher proposal.
- `tools/propose_solver.py` — 12-gate quality review, never auto-merges.
- `waggledance/core/learning/composition_graph.py` — typed DAG over the
  existing library with typed `useful_composite_paths` and `BridgeCandidate`.
- `tools/hex_subdivision_plan.py` — candidate subdivisions (document, not code change).
- `tools/phase8_capability_report.py` — offline capability-growth metrics
  computed from existing artifacts, matching the documented 13-signal
  surface (see `docs/architecture/PHASE8_METRICS.md`).
- `tools/run_honeycomb_400h_campaign.py` — segment-aware campaign
  scaffolding; never auto-starts without `--confirm-start`.

Design document: [`docs/architecture/HONEYCOMB_SOLVER_SCALING.md`](docs/architecture/HONEYCOMB_SOLVER_SCALING.md).
Validation: [`docs/runs/phase8_validation.md`](docs/runs/phase8_validation.md).

## API

REST + WebSocket on port 8000. Key groups:

| Group | Examples |
|-------|----------|
| Core | `POST /api/chat`, `GET /api/status`, `GET /api/heartbeat` |
| Ops | `GET /api/ops` — live FlexHW tier + AutoThrottle telemetry |
| Autonomy | `/api/autonomy/status`, `/api/autonomy/kpis`, `/api/autonomy/learning/run` |
| Hologram | `GET /api/hologram/state` (32 nodes + node_meta), `GET /hologram` |
| Storage | `GET /api/storage/health`, `POST /api/storage/wal-checkpoint` |
| Introspection | `/api/magma/*`, `/api/graph/*`, `/api/trust/*`, `/api/cross-agent/*`, `/api/analytics/*` |
| Profiles | `GET /api/profiles` — `{active, configured, restart_required}` |
| Feeds | `GET /api/feeds` — config-based sources with per-source freshness |
| Learning | `/api/learning/state-machine`, `/api/capabilities/state` |
| Sensors | `/api/sensors`, `/api/sensors/home`, `/api/sensors/camera/events` |
| Hybrid | `/api/hybrid/status`, `/api/hybrid/topology`, `/api/hybrid/cells` — hex-cell FAISS retrieval |

WebSocket at `ws://localhost:8000/ws` for real-time brain updates, chat streaming, alerts.

See [`docs/API.md`](docs/API.md) for full reference.

## Security

- **Auth** — HttpOnly session cookie (SameSite=Strict, 1 h TTL) for the browser; Bearer token for cURL/scripts/CI. API key auto-generated on first start
- **No browser-visible secrets** — master key never appears in served HTML, inline JS, localStorage, or sessionStorage
- **No frontend Bearer construction** — all browser fetches use `credentials: 'same-origin'`
- **No `?token=` in frontend WebSocket** — browser WS connects clean; token parameter is accepted server-side for scripts only
- **No eval()** — AST-based whitelist expression evaluator (`core/safe_eval.py`)
- **Safe Action Bus** — all write operations go through policy → risk → approval chain
- **OOM protection** — ResourceGuard with adaptive throttling and emergency GC
- **MQTT TLS** — enabled by default (port 8883)

## Testing

```bash
python -m pytest -q                            # Full suite
python -m pytest tests/autonomy/ -v            # Autonomy runtime
python -m pytest tests/contracts/ -v           # Port contract tests
python -m pytest tests/continuity/ -v          # Continuity regression
python tools/waggle_backup.py --tests-only     # Legacy component suites
```

## License

**Dual-licensed:**

| Component | License | File |
|-----------|---------|------|
| Open core (infrastructure, adapters, tests, API) | Apache 2.0 | [`LICENSE`](LICENSE) |
| Protected modules (dream mode, consolidator, meta-optimizer, projections, MAGMA core) | BUSL 1.1 | [`LICENSE-BUSL.txt`](LICENSE-BUSL.txt) |

Protected module list: [`LICENSE-CORE.md`](LICENSE-CORE.md)

BUSL change date: **2030-03-18** — becomes Apache 2.0 automatically.

**Personal non-commercial use of protected modules is permitted.**
Commercial licensing: see [`COMMERCIAL-USE.md`](COMMERCIAL-USE.md) or contact janikorpi@hotmail.com.

## Credits

Built by **Jani Korpi** ([Ahkerat Mehilaiset](https://github.com/Ahkeratmehilaiset), Helsinki) with [Claude Code](https://claude.ai/claude-code).

---

*WaggleDance — Local. Auditable. Autonomous.*
