# WaggleDance Swarm AI

> Local-first AI runtime with solver-first routing, self-training specialists, overnight dream learning, and full MAGMA audit trail.

[![Tests](https://img.shields.io/badge/tests-4772%20passing-brightgreen)]()
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)]()
[![License](https://img.shields.io/badge/license-Apache%202.0%20%2B%20BUSL%201.1-orange)]()

## What It Does

WaggleDance routes every query through **specialized solver engines first** — thermal,
causal, anomaly detection, scheduling, energy forecasting — before falling back to an LLM.
A solver router scores each engine's fit, runs the best match, then a verifier checks the
result against the world model. The LLM is the last resort, not the first.

The runtime **trains its own specialist models overnight**. 14 sklearn-based models
(route classifier, anomaly detector, baseline scorer, approval predictor, etc.) promote
through a canary lifecycle: train on accumulated case trajectories, validate on holdout
data, canary for 48h, then promote or rollback automatically. A meta-optimizer tunes
hyperparameters from canary results. Dream Mode runs counterfactual simulations on
failed missions — "what if we had routed differently?" — and feeds better strategies back.

Every query — whether answered by a solver, specialist, or LLM — produces a
**CaseTrajectory** recording the goal, selected route, response, and quality grade.
Chat traffic feeds cases via `build_from_legacy()`; autonomy missions record the full
lifecycle including world snapshots and verifier outcomes. The night learning pipeline
automatically ingests pending cases from the SQLite store using a watermark to prevent
reprocessing. A background scheduler triggers learning every 10 minutes when pending
cases accumulate and the runtime is idle. All cases are stored in MAGMA,
a 5-layer audit/replay/provenance architecture.
MAGMA provides append-only audit logging, mission-level replay, memory overlays, 9-tier
provenance tracking, and multi-dimensional trust scoring. The Hologram Brain page visualizes
all 32 system nodes in real-time across 4 concentric rings with per-node state metadata.

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
| Version | v3.3.8 |
| Architecture | Hexagonal — DI container, port/adapter, single-product |
| Runtime | ElasticScaler + AdaptiveThrottle + ResourceGuard via DI |
| Specialist models | 14 (real sklearn training, canary lifecycle) |
| Production validated | 30 h soak — 3181/3181 OK, 0 restarts, 175 learning cycles |
| Cutover | Full autonomy mode enabled |

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
