# WaggleDance Swarm AI

> Local-first AI runtime with solver-first routing, self-training specialists, overnight dream learning, and full MAGMA audit trail.

[![Tests](https://img.shields.io/badge/tests-4649%20passing-brightgreen)]()
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

Every action produces a **CaseTrajectory** — a structured record of goal, world snapshot,
action taken, and outcome — stored in MAGMA, a 5-layer audit/replay/provenance architecture.
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
  adapters/          LLM (Ollama), memory (ChromaDB), sensors, HTTP, config
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
Docked panel with 8 tabs + Chat. Bilingual FI/EN. No fake activation floors.

## Current Status

| Metric | Value |
|--------|-------|
| Version | v3.3.5 |
| Pytest tests | 4649 passing |
| Legacy test suites | 87 suites, 2754 tests |
| Autonomy modules | 42 validated |
| Specialist models | 14 (real sklearn training) |
| Gold routing rate | 76% |
| Production validated | 10h overnight, 99.6% uptime |
| Cutover | Full autonomy mode enabled |

## API

70+ REST endpoints on port 8000. Key groups:

| Group | Examples |
|-------|----------|
| Core | `POST /api/chat`, `GET /api/status`, `GET /api/heartbeat` |
| Autonomy | `/api/autonomy/status`, `/api/autonomy/kpis`, `/api/autonomy/learning/run` |
| Hologram | `GET /api/hologram/state` (32 nodes + node_meta), `GET /hologram` |
| MAGMA | `/api/magma/stats`, `/api/magma/audit`, `/api/magma/overlays` |
| Trust | `/api/trust/ranking`, `/api/trust/agent/{id}` |
| Learning | `/api/learning/state-machine`, `/api/capabilities/state` |
| Sensors | `/api/sensors`, `/api/sensors/home`, `/api/sensors/camera/events` |

WebSocket at `ws://localhost:8000/ws` for real-time brain updates, chat streaming, alerts.

See [`docs/API.md`](docs/API.md) for full reference.

## Security

- **No eval()** — AST-based whitelist expression evaluator (`core/safe_eval.py`)
- **Safe Action Bus** — All write operations go through policy -> risk -> approval chain
- **MQTT TLS** — Enabled by default (port 8883)
- **OOM protection** — ResourceGuard with throttling and emergency GC
- **Auth** — HttpOnly session cookie for browser; Bearer token for cURL/scripts/CI. API key auto-generated on first start, never reaches the browser

## Testing

```bash
python -m pytest -q                            # 4649 tests
python -m pytest tests/autonomy/ -v            # Autonomy tests (1600+)
python -m pytest tests/contracts/ -v           # Port contract tests
python -m pytest tests/continuity/ -v          # v3.2 continuity tests
python tools/waggle_backup.py --tests-only     # Legacy suite (87 suites)
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
